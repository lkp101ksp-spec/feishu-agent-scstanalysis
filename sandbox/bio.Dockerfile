# Phase 20 bio 镜像：scanpy 单细胞分析栈（spec 2026-09-01 phase20 §3.1）
# 构建：docker build -t feishu-research-agent/bio:cpu-latest sandbox -f sandbox/bio.Dockerfile
# GPU 预留：后续 rapids-singlecell 栈另建 bio:gpu-latest tag，settings.bio_image 可切
FROM python:3.12-slim

# 清华源装单细胞栈（项目惯例：pip 默认走清华源）
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    numpy pandas scipy matplotlib h5py \
    anndata scanpy leidenalg igraph

# Phase 31 富集分析：gseapy + Enrichr 基因集预取
# （构建期有网时下载 hallmark/GO BP/KEGG 三库存 /opt/gene_sets/，
#   运行期容器 --network none 离线可用；源不可达时 build 报错重试即可）
COPY fetch_gene_sets.py /tmp/fetch_gene_sets.py
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple gseapy \
    && python /tmp/fetch_gene_sets.py \
    && rm /tmp/fetch_gene_sets.py

# Phase 33 批次整合：bbknn（独立层，保上方 gene_sets 缓存层）。
# annoy（bbknn 硬依赖）无 manylinux 轮需源码编译——同层临时装 g++，
# 编译完成后 purge，层体积近似不变。
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ \
    && pip install --no-cache-dir \
        -i https://pypi.tuna.tsinghua.edu.cn/simple bbknn \
    && apt-get purge -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*

# Phase 34 B 类分析：liana（细胞通讯，内置 consensus/mouseconsensus
# 资源库随包分发，容器断网可用）。statsmodels/sklearn 已由 scanpy
# 传递依赖带入；liana 依赖 plotnine/kneed 等均为纯 Python/manylinux 轮，
# 无编译需求（若构建报编译错误，按 bbknn 层先例同层临时装 g++）。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple liana

# Phase 35 自动注释：celltypist + 模型构建期预取（运行期断网可用）。
# 模型下载自 celltypist.cog.sanger.ac.uk；不可达时 build 报错重试即可。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple celltypist \
    && python -c "from celltypist import models; \
        models.download_models(model=['Immune_All_Low.pkl', 'Immune_All_High.pkl'])" \
    && mkdir -p /opt/celltypist_models \
    && cp /root/.celltypist/data/models/*.pkl /opt/celltypist_models/

# Phase 35 双联体：scrublet（依赖 annoy 无 manylinux 轮需源码编译——
# 同层临时装 g++，编译完成后 purge，bbknn 层先例）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ \
    && pip install --no-cache-dir \
        -i https://pypi.tuna.tsinghua.edu.cn/simple scrublet \
    && apt-get purge -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*

# Phase 36 调控网络：pyscenic 0.12.1。锁 setuptools<81（84+ 删了
# pkg_resources，ctxcore import 即炸）；sitecustomize 补 numpy>=1.24
# 移除的别名（np.object/np.float），放 site-packages 由 site 自动导入
# 以覆盖 dask worker 子进程。宿主 probe 实测：pandas 2.3.3/numpy 2.5.2/
# dask 2026.8.0 下三幕全绿（GRNBoost2 走 create_graph 绕路、prune2df
# from_delayed 物化 monkeypatch——均在 scenic.py 内）。
# 注：ctxcore 0.2.0（当前 PyPI 最新）增量 prefetch 缓存有 bug
# （difference 未排除已加载列 → append_column 重名 → select KeyError），
# 0.1.1 sdist 在 Py3.12 下拉老 numpy 源码编译失败——不修版本，
# 在 scenic.py _prune 内 monkeypatch 禁用增量缓存（每次全量重读列）。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    pyscenic "setuptools<81"
COPY scenic_site/sitecustomize.py /usr/local/lib/python3.12/site-packages/sitecustomize.py

# Phase 37 多组学：muon WNN（探针实测 muon 0.1.9+mudata 0.4.1 与
# pandas 2.3.3/numpy 2.5.2 兼容；n_multineighbors<n_obs 防御在脚本内）。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple muon

# Phase 37 虚拟敲除：R 4.5 + CRAN scTenifoldKnk 1.1（保真路线——算法链
# 封装在 R 包内，Python 端口维护停滞。P3M trixie 二进制优先（设 UA），
# 依赖链含需编译包（igraph 等）→ r-base-dev/g++ 同层装完即 purge。
# 探针实测：基底 trixie、apt r-base-core=4.5.0、P3M trixie 源 200）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends r-base-core r-base-dev g++ \
    && Rscript -e "options(HTTPUserAgent=sprintf('R/%s R (%s)', getRversion(), paste(getRversion(), R.version['platform'], R.version['arch'], R.version['os']))); install.packages('scTenifoldKnk', repos='https://packagemanager.posit.co/cran/__linux__/trixie/latest')" \
    && Rscript -e "library(scTenifoldKnk); cat('scTenifoldKnk', as.character(packageVersion('scTenifoldKnk')), 'ok\n')" \
    && apt-get purge -y --no-install-recommends r-base-dev g++ \
    && rm -rf /var/lib/apt/lists/*

# B1 CNV 推断：infercnvpy（成熟默认）+ cnvturbo（对齐 R inferCNV HMM i6）
# 双后端。纯 CPU 工具；若装包报原生编译缺失，按 bbknn 层先例同层临时
# 装 g++ 编译后 purge。两层分离：改坐标脚本不重装 pip。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple infercnvpy==0.6.1 cnvturbo==0.3.0 \
    && python -c "import infercnvpy, cnvturbo; print('cnv deps ok')"
COPY fetch_gene_pos.py /tmp/fetch_gene_pos.py
RUN python /tmp/fetch_gene_pos.py && rm /tmp/fetch_gene_pos.py

# Palantir 拟时序引擎（sc_pseudotime 第三引擎，spec
# 2026-09-13-sc-palantir-engine-design.md）。探针钉注：py3.12 +
# numpy 2.5.2 下 palantir 1.4.5 零 pin 冲突（mellon 1.7.1 已修
# numpy 2 兼容）；闭包 jax/jaxlib 0.11.1+jaxopt 0.8.5+ml_dtypes
# 0.6.0+igraph 1.0.0 约 500MB，全 manylinux 轮零编译、零运行时
# 下载（断网实跑 300 细胞全工作流 rho=0.9917，palantir_trial.py）。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple palantir==1.4.5 \
    && python -c "import palantir; print('palantir ok')"

# Slingshot 拟时序引擎（sc_pseudotime 第四引擎，spec
# 2026-09-13-sc-slingshot-engine-design.md）。探针四轮钉注（测试总结
# 第三十五段）：R 4.5 配对 Bioc 3.21（清华/USTC 仅托管当前 3.23 分支，
# 3.21 唯官方仓可达）；GenomeInfoDbData 在独立 data/annotation 仓；
# 预装 igraph.so 需 libxml2/libglpk40 运行期库（常驻不 purge）；
# g++/r-base-dev 仅编译期（74 包全源码，实测 3.4min/67MB）装完 purge。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       r-base-dev g++ libxml2 libglpk40 \
    && Rscript -e "options(repos=c(CRAN='https://mirrors.tuna.tsinghua.edu.cn/CRAN/', Bioc='https://bioconductor.org/packages/3.21/bioc', BiocAnn='https://bioconductor.org/packages/3.21/data/annotation'), timeout=600); install.packages('slingshot', Ncpus=4)" \
    && Rscript -e "library(slingshot); cat('slingshot', as.character(packageVersion('slingshot')), 'ok\n')" \
    && apt-get purge -y --no-install-recommends r-base-dev g++ \
    && rm -rf /var/lib/apt/lists/*

# slingshot 运行期增补：DelayedMatrixStats/sparseMatrixStats 为
# Suggests 级依赖——library(slingshot) 自检不触发，slingshot() 实际
# 计算路径 loadNamespace 必需（冒烟场景⑭断网首跑曝缺，补装后
# SLING_DONE 2 双谱系绿，_eval/probe_sling_runtime.sh 留证）。
# 独立层保上方 74 包装包缓存。
RUN apt-get update \
    && apt-get install -y --no-install-recommends r-base-dev g++ \
    && Rscript -e "options(repos=c(CRAN='https://mirrors.tuna.tsinghua.edu.cn/CRAN/', Bioc='https://bioconductor.org/packages/3.21/bioc'), timeout=600); install.packages(c('DelayedMatrixStats','sparseMatrixStats'), Ncpus=4)" \
    && Rscript -e "library(DelayedMatrixStats); library(sparseMatrixStats); cat('sling runtime deps ok\n')" \
    && apt-get purge -y --no-install-recommends r-base-dev g++ \
    && rm -rf /var/lib/apt/lists/*

# CellChat v2（2.2.0.9001，jinworks fork GitHub 源——CRAN 无 v2；探针
# r1-r9 递进排障钉注见 测试总结第五十五段）。网络分层：apt 走清华
# Debian 直连（代理对 deb.debian.org 502 抖动）、CRAN 清华直连、
# Bioc 官方仓+GitHub 走 build 代理（ARG PROXY，默认宿主 clash 的
# netsh portproxy 17891→17890，与代理监听地址解耦）。Bioc repo 必须
# 带 /bioc 层，否则 PACKAGES 404（r5-r7 误诊根因）。
# 编译链 16 包逐轮定位：xml2/uv/cairo/fontconfig dev → C++ 工具链 →
# ragg 五件套 freetype/png/tiff/jpeg/webp → units → cmake(nloptr)。
# dependencies=TRUE 全家桶（+200 包含 Seurat/tidyverse，探针实测
# 11min/轮全绿）；后续瘦身轮可试 FALSE 只装 hard deps。
# purge 纪律：只 purge 显式清单（编译工具链 + r-base-dev + *-dev），
# 不跑 autoremove——dev 的运行库依赖（ragg/textshaping/igraph 的 so
# 链）自然留存，免去逐库 apt-mark manual 的 trixie 包名漂移风险；
# 末尾 library(CellChat) 全量自检兜底，so 链断则 build 失败。
# CellChatDB v2（3233 互作）随包离线内置，运行期容器断网可用。
ARG PROXY=http://host.docker.internal:17891
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update -qq -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false \
    && apt-get install -y -qq --no-install-recommends \
       -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false \
       build-essential pkg-config gfortran cmake r-base-dev \
       libxml2-dev libuv1-dev libfontconfig1-dev libcairo2-dev \
       libharfbuzz-dev libfribidi-dev libgit2-dev libpng-dev \
       libfreetype6-dev libudunits2-dev libtiff5-dev libjpeg-dev libwebp-dev \
    && HTTP_PROXY=${PROXY} HTTPS_PROXY=${PROXY} \
       http_proxy=${PROXY} https_proxy=${PROXY} \
       Rscript -e "options(repos=c(CRAN='https://mirrors.tuna.tsinghua.edu.cn/CRAN', Bioc='https://bioconductor.org/packages/3.21/bioc'), Ncpus=4, timeout=1800); install.packages('remotes'); remotes::install_github('jinworks/CellChat', upgrade='never', dependencies=TRUE)" \
    && apt-get purge -y --no-install-recommends \
       build-essential g++ gfortran cmake pkg-config r-base-dev \
       libxml2-dev libuv1-dev libfontconfig1-dev libcairo2-dev \
       libharfbuzz-dev libfribidi-dev libgit2-dev libpng-dev \
       libfreetype6-dev libudunits2-dev libtiff5-dev libjpeg-dev libwebp-dev \
    && rm -rf /var/lib/apt/lists/* \
    && Rscript -e "library(CellChat); cat('CellChat', as.character(packageVersion('CellChat')), 'db_rows', nrow(CellChatDB.human$interaction), '\n')"

# monocle3 第四轨迹引擎（sc_pseudotime engine='monocle3'，重启评估
# 探针两轮钉注见 测试总结第六十段）：cole-trapnell-lab GitHub 源
# （CRAN 无）。apt 链 = cellchat r1-r9 dev 链 + gdal/geos/proj（sf
# 依赖）；探针实测 sf 8.1min + monocle3 链 13min（Ncpus=8 口径，
# 本层 Ncpus=4 更慢，CI manual job 预算 50→71min）。purge 同
# cellchat 纪律：只 purge 显式清单、不 autoremove——sf/units/
# ggrastr 的运行 so 链（gdal/geos/proj/cairo 运行库）自然留存。
# 2026-09-19 CI 冷构建实证（run 35448479226）：TUNA CRAN 并行
# 下载抖动——44 包同波下载，statmod/futile.logger/snow 同秒 4.6s
# 失败→limma/BiocParallel 链式缺依赖→monocle3 R CMD INSTALL 崩。
# install 改 R 级重试封装 ir()（每波只补缺、波间 sleep 15s、
# 6 波兜底 stop），monocle3 本体同样 3 次重试——对齐 zenodo 504
# 教训：层内下载必须自带重试，构建期网络调用不赌单次成功。
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update -qq -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false \
    && apt-get install -y -qq --no-install-recommends \
       -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false \
       build-essential pkg-config gfortran cmake r-base-dev \
       libgdal-dev libgeos-dev libproj-dev \
       libxml2-dev libuv1-dev libfontconfig1-dev libcairo2-dev \
       libharfbuzz-dev libfribidi-dev libgit2-dev libpng-dev \
       libfreetype6-dev libudunits2-dev libtiff5-dev libjpeg-dev libwebp-dev \
    && HTTP_PROXY=${PROXY} HTTPS_PROXY=${PROXY} \
       http_proxy=${PROXY} https_proxy=${PROXY} \
       Rscript -e "options(repos=c(CRAN='https://mirrors.tuna.tsinghua.edu.cn/CRAN', Bioc='https://bioconductor.org/packages/3.21/bioc'), Ncpus=4, timeout=1800); ir <- function(p,n=6){for(i in 1:n){m<-p[!vapply(p,function(x) requireNamespace(x,quietly=TRUE),logical(1))];if(!length(m))return(invisible(0));cat('[ir wave',i,'] installing:',paste(m,collapse=','),'\n');install.packages(m);Sys.sleep(15)};stop('deps missing after retries: ',paste(p[!vapply(p,function(x) requireNamespace(x,quietly=TRUE),logical(1))],collapse=','))}; ir('sf'); for(j in 1:3){if(requireNamespace('monocle3',quietly=TRUE))break;cat('[monocle3 try',j,']\n');remotes::install_github('cole-trapnell-lab/monocle3',upgrade='never')}; if(!requireNamespace('monocle3',quietly=TRUE)) stop('monocle3 missing after retries')" \
    && apt-get purge -y --no-install-recommends \
       build-essential g++ gfortran cmake pkg-config r-base-dev \
       libgdal-dev libgeos-dev libproj-dev \
       libxml2-dev libuv1-dev libfontconfig1-dev libcairo2-dev \
       libharfbuzz-dev libfribidi-dev libgit2-dev libpng-dev \
       libfreetype6-dev libudunits2-dev libtiff5-dev libjpeg-dev libwebp-dev \
    && rm -rf /var/lib/apt/lists/* \
    && Rscript -e "library(monocle3); cat('monocle3', as.character(packageVersion('monocle3')), 'ok\n')"

# CytoTRACE2 绝对干性打分（独立工具 sc_cytotrace2，2026-09-17 死刑
# 推翻翻案探针钉注见 测试总结六补记）：R 版 digitalcytometry GitHub
# 源，模型参数 parameter_dict_19.rds 等 5 件随 inst/extdata 分发
# （离线审计零运行期下载；断网 10k 实跑 11.9min/4 核全绿）。
# Depends 大头（Seurat/SeuratObject/RSpectra/RANN/stringr 等）
# cellchat 层已带，实缺 HiClimR/Rfast：ncdf4→libnetcdf-dev、
# RcppParallel→cmake、HiClimR.f90→gfortran。tarball 走 api.github.com
# 端点（codeload 直连对部分网络不稳，探针实证；GitHub CI 无墙直通）。
# purge 同 cellchat 纪律：只 purge 显式清单不 autoremove，libnetcdf
# 运行库自然留存；末尾 library 自检兜底 so 链。
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update -qq -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false \
    && apt-get install -y -qq --no-install-recommends \
       -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false \
       build-essential pkg-config gfortran cmake r-base-dev libnetcdf-dev \
    && Rscript -e "options(repos=c(CRAN='https://mirrors.tuna.tsinghua.edu.cn/CRAN/'), Ncpus=4, timeout=1800); install.packages(c('HiClimR','Rfast')); options(timeout=900); download.file('https://api.github.com/repos/digitalcytometry/cytotrace2/tarball/main', '/tmp/c2.tar.gz', mode='wb'); untar('/tmp/c2.tar.gz', exdir='/tmp'); d <- list.dirs('/tmp', recursive=FALSE); d <- d[grepl('cytotrace2', basename(d))][1]; install.packages(file.path(d, 'cytotrace2_r'), repos=NULL, type='source'); unlink(c('/tmp/c2.tar.gz', d), recursive=TRUE)" \
    && apt-get purge -y --no-install-recommends \
       build-essential g++ gfortran cmake pkg-config r-base-dev libnetcdf-dev \
    && rm -rf /var/lib/apt/lists/* \
    && Rscript -e "suppressMessages(library(CytoTRACE2)); cat('CytoTRACE2', as.character(packageVersion('CytoTRACE2')), 'ok\n')"

# NicheNet 配体活性优先级（独立工具 sc_nichenet，2026-09-18 八补记
# q2 探针终判落地）：nichenetr GitHub 源（master 硬依赖带 mlrMBO——
# CRAN 已下架，须 remotes::install_version 装 archive 组 BBmisc/
# ParamHelpers/mlr/mlrMBO；探针三轮钉注见 build_nnprobe.sh）。
# gdtools 编译链 cairo/fontconfig/xt/freetype（monocle3 层 purge 过
# -dev，此处构建期重装再 purge，运行库自然留存）。Zenodo 7074291
# 先验四件构建期烘焙 /opt/nichenet_prior/（human lt 250MB/mouse
# lt 182MB/lr 各 20-30KB；离线审计零运行期下载——断网自证 A2M rank
# 1/68 aupr 0.985）。外网下载统一走 dl() 五次重试+20s 退避+构建代理
# （2026-09-19 CI 首撞 zenodo 504 钉档：runner 无层缓存每次裸连，单次
# 抖动即整层报废；同日本地重建 zenodo 直连 5 连败——"直连实证"已过
# 期，GitHub/Zenodo 两段统一走 ${PROXY}，dl() 只防抖不救路由）。
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update -qq -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false \
    && apt-get install -y -qq --no-install-recommends \
       -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false \
       build-essential pkg-config gfortran \
       libcairo2-dev libxt-dev libfontconfig1-dev libfreetype6-dev \
    && Rscript -e "options(repos=c(CRAN='https://mirrors.tuna.tsinghua.edu.cn/CRAN/'), Ncpus=4, timeout=1800); install.packages(c('gdtools','shadowtext','fdrtool','Hmisc','caret','randomForest','DiagrammeR','parallelMap','emoa','DiceKriging','ggnewscale','remotes'))" \
    && Rscript -e "options(repos=c(CRAN='https://mirrors.tuna.tsinghua.edu.cn/CRAN/'), Ncpus=4, timeout=1800); for (p in c('BBmisc','ParamHelpers','mlr','mlrMBO')) { if (!requireNamespace(p, quietly=TRUE)) remotes::install_version(p, upgrade='never', quiet=TRUE) }" \
    && HTTP_PROXY=${PROXY} HTTPS_PROXY=${PROXY} http_proxy=${PROXY} https_proxy=${PROXY} \
       Rscript -e "options(repos=c(CRAN='https://mirrors.tuna.tsinghua.edu.cn/CRAN/'), timeout=1800); dl <- function(u, d, tries=5) { for (i in seq_len(tries)) { ok <- tryCatch({ download.file(u, d, mode='wb'); file.exists(d) && file.size(d) > 0 }, error=function(e) FALSE); if (isTRUE(ok)) return(invisible(TRUE)); Sys.sleep(20) }; stop('download failed: ', u) }; dl('https://api.github.com/repos/saeyslab/nichenetr/tarball/master', '/tmp/nn.tar.gz'); untar('/tmp/nn.tar.gz', exdir='/tmp'); d <- list.dirs('/tmp', recursive=FALSE); d <- d[grepl('nichenetr', basename(d))][1]; install.packages(d, repos=NULL, type='source', dependencies=FALSE); unlink(c('/tmp/nn.tar.gz', d), recursive=TRUE)" \
    && mkdir -p /opt/nichenet_prior \
    && HTTP_PROXY=${PROXY} HTTPS_PROXY=${PROXY} http_proxy=${PROXY} https_proxy=${PROXY} \
       Rscript -e "options(timeout=3600); dl <- function(u, d, tries=5) { for (i in seq_len(tries)) { ok <- tryCatch({ download.file(u, d, mode='wb'); file.exists(d) && file.size(d) > 0 }, error=function(e) FALSE); if (isTRUE(ok)) return(invisible(TRUE)); Sys.sleep(20) }; stop('download failed: ', u) }; for (f in c('ligand_target_matrix_nsga2r_final.rds','lr_network_human_21122021.rds','ligand_target_matrix_nsga2r_final_mouse.rds','lr_network_mouse_21122021.rds')) { dl(paste0('https://zenodo.org/records/7074291/files/', f), file.path('/opt/nichenet_prior', f)) }" \
    && apt-get purge -y --no-install-recommends \
       build-essential g++ gfortran pkg-config \
       libcairo2-dev libxt-dev libfontconfig1-dev libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/* \
    && Rscript -e "suppressMessages(library(nichenetr)); cat('nichenetr', as.character(packageVersion('nichenetr')), 'ok\n'); lt_h <- readRDS('/opt/nichenet_prior/ligand_target_matrix_nsga2r_final.rds'); cat('lt_human', nrow(lt_h), 'x', ncol(lt_h), '; lt_mouse', nrow(readRDS('/opt/nichenet_prior/ligand_target_matrix_nsga2r_final_mouse.rds')), '; lr ok\n')"

# Phase 69 整合引擎二：harmonypy（sc_integrate/st_integrate method=
# 'harmony'，spec 2026-09-19-st-integrate-design.md）。钉 >=2 下限
# （用户口径"v2 以上"，2026-09-19）：2.x 为重写版（Z_corr=(cells,pcs)
# 不转置；清华源无 0.3/0.4 线），sc_integrate/st_integrate 直调
# run_harmony 并按形状自适应，不依赖 scanpy 包装。
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple "harmonypy>=2" \
    && python -c "import harmonypy, importlib.metadata as im; print('harmonypy', im.version('harmonypy'), 'ok')"

# Phase 71 细胞因子信号：CytoSig ridge + data_significance（GSL C
# 扩展，numpy 1.x 编译期硬约束）→ 独立 venv，主环境零污染。numpy<2
# 与 CytoSig 同命令约束：--no-build-isolation 只管构建期，安装期 pip
# 仍会把 venv numpy 升到 2.x（C 扩展编译于 1.x，运行时 ABI 崩）。
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgsl-dev build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv /opt/cytosig_env \
    && /opt/cytosig_env/bin/pip install --no-cache-dir \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        "numpy<2" pandas setuptools wheel \
    && /opt/cytosig_env/bin/pip install --no-cache-dir --no-build-isolation \
        -i https://pypi.tuna.tsinghua.edu.cn/simple "numpy<2" CytoSig \
    && /opt/cytosig_env/bin/python -c "import CytoSig, numpy; print('cytosig venv ok, numpy', numpy.__version__)"

# Phase 72 sc_genescore 通路活性：decoupler + PROGENy 模型快照
# （st 镜像 Phase 50 同款两层分离——改快照脚本不重装 pip；快照需构建期
#   网络（omnipathdb.org），运行期断网读 TSV；fetch_progeny 与 st 共用）
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple decoupler==2.2.0 \
    && python -c "import decoupler; print('decoupler', decoupler.__version__)"
COPY fetch_progeny.py /tmp/fetch_progeny.py
RUN python /tmp/fetch_progeny.py && rm /tmp/fetch_progeny.py

# 非 root 用户 + 可写目录（与 kernel 镜像惯例一致）
RUN useradd -u 1000 -m bio \
    && mkdir -p /ws /data /tmp/mpl \
    && chown -R bio /ws /tmp/mpl

# matplotlib 无头模式 + 缓存目录指向可写 tmp
ENV MPLCONFIGDIR=/tmp/mpl PYTHONUNBUFFERED=1

WORKDIR /ws

# 固化参数化脚本（BioRunner 调 python /opt/sc_tools/<name>.py）
COPY sc_tools/ /opt/sc_tools/
COPY r_tools/ /opt/r_tools/

# 短命容器：跑完即退（--rm），无 CMD 保活需求
CMD ["python", "/opt/sc_tools/load.py"]
