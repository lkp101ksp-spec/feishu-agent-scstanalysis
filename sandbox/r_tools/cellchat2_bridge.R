# CellChat v2 (2.2.0.9001) 桥接：sc_cellchat_v2 / st_cellchat_v2 共用
# （Phase 57；镜像层与探针钉注见 测试总结第五十五段）。
# 与 slingshot_bridge/knk.R 同契约：argv 传参、CSV/mtx 进出、Python 侧画图。
# v2.2 API 差异钉注（镜像内 args() 实测）：
#   - computeCommunPathway → computeCommunProbPathway（改名）
#   - subsetData(object, features=NULL)（v1 的 search/key 参数已删，
#     赋 cc@DB 后直接 subsetData 即按 DB 交集裁表达）
#   - computeCommunProb 新增空间参数：distance.use/interaction.range/
#     scale.distance/contact.*（默认 distance.use=TRUE——非空间数据
#     createCellChat 无 coordinates 时不影响；raw.use v2 默认翻转为
#     TRUE，桥内显式 FALSE 对齐 v1 口径=用 normalized 输入）
#   - DB 取用必须 CellChat:: 限定（裸名惰性加载偶发 $interaction 为
#     NULL，探针实测三路验证）
# argv: in_dir out_dir species(human|mouse) min_cells spatial(0|1)
#       [interaction_range] [unit_scale]
# 输入（Python 侧导出，同一细胞顺序对齐）：
#   expr.mtx + features.tsv + barcodes.tsv —— genes × cells MatrixMarket
#   meta.csv(cell,type)；spatial=1 时 spatial.csv(cell,x,y)
#   unit_scale：坐标→µm 缩放（visium fullres 像素时 Python 侧算好传入）
# 输出：lr.csv / pathway.csv / centrality.csv / counts.csv /
#       weights.csv / summary.txt；终行 CELLCHAT2_DONE
args_c <- commandArgs(trailingOnly = TRUE)
in_dir <- args_c[1]
out_dir <- args_c[2]
species <- tolower(args_c[3])
min_cells <- as.integer(args_c[4])
spatial <- identical(args_c[5], "1")
irange <- if (length(args_c) >= 6 && nzchar(args_c[6])) as.numeric(args_c[6]) else 250
uscale <- if (length(args_c) >= 7 && nzchar(args_c[7])) as.numeric(args_c[7]) else 1

suppressMessages(library(Matrix))
suppressMessages(library(CellChat))

m <- Matrix::readMM(file.path(in_dir, "expr.mtx"))
m <- as(m, "dgCMatrix")
rownames(m) <- readLines(file.path(in_dir, "features.tsv"))
colnames(m) <- readLines(file.path(in_dir, "barcodes.tsv"))
meta <- read.csv(file.path(in_dir, "meta.csv"), row.names = 1,
                 check.names = FALSE)
cells <- intersect(colnames(m), rownames(meta))
m <- m[, cells, drop = FALSE]
meta <- meta[cells, , drop = FALSE]

db <- if (species == "mouse") CellChat::CellChatDB.mouse else CellChat::CellChatDB.human

cc <- if (spatial) {
  sp <- as.matrix(read.csv(file.path(in_dir, "spatial.csv"),
                           row.names = 1, check.names = FALSE))[cells, , drop = FALSE]
  # v2.2 契约：spatial.factors 必须含 ratio（坐标→µm 缩放，visium
  # fullres 像素时 Python 侧算好传入）与 tol（knn 距离相对
  # interaction.range 的容差，取 irange/5）
  createCellChat(object = m, meta = meta, group.by = "type",
                 datatype = "spatial", coordinates = sp,
                 spatial.factors = list(ratio = uscale,
                                        tol = max(1, irange / 5)))
} else {
  createCellChat(object = m, meta = meta, group.by = "type")
}
# v2.2 坑：矩阵输入不填 @data.raw（0×0），且 raw.use=FALSE（读 @data）
# 路径实测报 "no rows to aggregate"——把 normalized 矩阵显式补进
# @data.raw 走默认 raw.use=TRUE；数学上等价 v1 normalized 口径
# （computeCommunProb 源码两分支都要 data/max(data)）。
# slot 类 AnyMatrix 直收 dgCMatrix——保持稀疏赋值，免 59900×24691
# 级别 as.matrix() 稠密化（真机实测一次分配 11 GiB，59900 r2 钉注）
cc@data.raw <- m
cc@DB <- db
cc <- subsetData(cc)
# presto（GitHub 源快速 Wilcoxon）未装——do.fast=FALSE 走标准 wilcox，
# 基因已被 subsetData 裁到 DB 交集（~千级），规模无碍
cc <- identifyOverExpressedGenes(cc, do.fast = FALSE)
cc <- identifyOverExpressedInteractions(cc)
# 空间模式需显式给 contact.knn.k（contact.dependent=TRUE 默认时
# computeRegionDistance 要求 contact.range 或 contact.knn.k 其一；
# 6=Visium spot 六邻接惯例）；单细胞分支源码内自行重置这些参数
cc <- if (spatial) {
  computeCommunProb(cc, type = "truncatedMean", trim = 0.1,
                    interaction.range = irange, contact.knn.k = 6)
} else {
  computeCommunProb(cc, type = "truncatedMean", trim = 0.1,
                    interaction.range = irange)
}
cc <- filterCommunication(cc, min.cells = min_cells)
cc <- computeCommunProbPathway(cc)
cc <- aggregateNet(cc)
cc <- netAnalysis_computeCentrality(cc)

dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)

# LR 级与通路级显著通讯表（subsetCommunication 版本间签名漂移，双兜底）
lr <- tryCatch(subsetCommunication(cc),
               error = function(e) subsetCommunication(cc, slots.name = "net"))
write.csv(lr, file.path(out_dir, "lr.csv"), row.names = FALSE)
pw <- tryCatch(subsetCommunication(cc, slot.name = "netP"),
               error = function(e) {
                 tryCatch(subsetCommunication(cc, slots.name = "netP"),
                          error = function(e2) NULL)
               })
if (!is.null(pw)) {
  write.csv(pw, file.path(out_dir, "pathway.csv"), row.names = FALSE)
}

# 网络中心性：v2.2 存 @netP$centr（v1 的 $centrality 已移位）——
# named list（per pathway）→ list（11 度量：outdeg/indeg/hub/authority/
# eigen/page_rank/betweenness/flowbet/info/±unweighted）→ named num(celltypes)
cen <- cc@netP$centr
rows <- list()
if (!is.null(cen)) {
  for (pw in names(cen)) {
    el <- cen[[pw]]
    for (nm in names(el)) {
      v <- el[[nm]]
      if (is.numeric(v) && !is.null(names(v))) {
        rows[[length(rows) + 1]] <- data.frame(
          pathway = pw, measure = nm, celltype = names(v),
          value = as.numeric(v), stringsAsFactors = FALSE)
      }
    }
  }
}
if (length(rows)) {
  write.csv(do.call(rbind, rows), file.path(out_dir, "centrality.csv"),
            row.names = FALSE)
}

# 聚合网络：net$count / net$weight（groups×source×target 3 维数组，取首组）
arr_to_mat <- function(arr3) {
  if (length(dim(arr3)) == 3) arr3[1, , , drop = FALSE] |> as.vector() |>
    matrix(nrow = dim(arr3)[2], dimnames = list(dimnames(arr3)[[2]],
                                                dimnames(arr3)[[3]]))
  else as.matrix(arr3)
}
cnt <- arr_to_mat(cc@net$count)
wgt <- arr_to_mat(cc@net$weight)
write.csv(data.frame(source = rownames(cnt), cnt, check.names = FALSE),
          file.path(out_dir, "counts.csv"), row.names = FALSE)
write.csv(data.frame(source = rownames(wgt), wgt, check.names = FALSE),
          file.path(out_dir, "weights.csv"), row.names = FALSE)

# summary：NROW 防御式兜底（惰性 DB 偶发 $interaction 取 NULL，
# sprintf(character(0)) 会静默吞行——探针冒烟实测）
safe_n <- function(x) {
  n <- tryCatch(NROW(x), error = function(e) NULL)
  if (is.null(n) || length(n) == 0 || is.na(n)) 0L else as.integer(n)
}
writeLines(c(
  sprintf("version=%s", as.character(packageVersion("CellChat"))),
  sprintf("mode=%s", if (spatial) "spatial" else "single"),
  sprintf("n_cells=%d", ncol(m)),
  sprintf("n_types=%d", length(levels(cc@idents))),
  sprintf("db_rows=%d", safe_n(db$interaction)),
  sprintf("n_lr=%d", safe_n(lr)),
  sprintf("n_pathway=%d", safe_n(pw))),
  file.path(out_dir, "summary.txt"))
cat("CELLCHAT2_DONE\n")
