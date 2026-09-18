# CytoTRACE2 绝对干性打分桥接（sc_cytotrace2，测试总结六补记死刑
# 推翻翻案落地）。
# argv: expr_mtx out_dir species ncores batch_size smooth_batch_size
# 输入：expr_mtx = 基因×cells 稀疏 MatrixMarket（integer field），
#   伴生同目录 genes.csv/barcodes.csv（各一列无表头）——大库适配
#   （q3 探针实证 cytotrace2() 直吃 dgCMatrix；dense tsv 旧口径
#   cells×genes×8B 在 59900 级会爆）；计数须 raw/CPM 整数——模型
#   要求不能 log/scaled，Python 侧定位链已保证。
# 输出：out_dir/c2_result.csv（cell + CytoTRACE2_Score/Potency/
#   Relative/preKNN_Score/preKNN_Potency 五列）+
#   out_dir/c2_meta.json（n_genes_mapped/n_genes_input 供上游上报；
#   映射口径与包内 preprocessData 同源 intersect——特征集 csv 基因名
#   在第二列且首行伪表头 `,0`，六补记探针钉注）。
# 模型参数 parameter_dict_19.rds 等 5 件随包 inst/extdata 分发，
# 断网可用（容器 --network none 实跑实证）。
suppressMessages(library(CytoTRACE2))
suppressMessages(library(Matrix))

args <- commandArgs(trailingOnly = TRUE)
expr_mtx <- args[1]
out_dir <- args[2]
species <- args[3]
ncores <- if (length(args) >= 4 && nzchar(args[4])) as.integer(args[4]) else 2L
if (is.na(ncores) || ncores < 1) ncores <- 2L
num_arg <- function(s) {
  if (nzchar(s)) {
    v <- as.integer(s)
    if (!is.na(v) && v > 0) return(v)
  }
  NULL
}
batch_size <- if (length(args) >= 5) num_arg(args[5]) else NULL
smooth_batch_size <- if (length(args) >= 6) num_arg(args[6]) else NULL

in_dir <- dirname(expr_mtx)
mat <- as(Matrix::readMM(expr_mtx), "dgCMatrix")
rownames(mat) <- readLines(file.path(in_dir, "genes.csv"))
colnames(mat) <- readLines(file.path(in_dir, "barcodes.csv"))
storage.mode(mat@x) <- "double"

call_args <- list(input = mat, species = species, ncores = ncores,
                  # 钉死 FALSE：默认 TRUE 走 predictData 内 mclapply
                  # fork，59900 级大矩阵 fork 子进程异常致输出空
                  # （q3 全量首跑实证，seq wrong sign 挂点）；FALSE
                  # 逐模型串行稳定，ncores 仍用于平滑段
                  parallelize_models = FALSE)
if (!is.null(batch_size)) call_args$batch_size <- batch_size
if (!is.null(smooth_batch_size))
  call_args$smooth_batch_size <- smooth_batch_size
res <- do.call(cytotrace2, call_args)

write.csv(data.frame(cell = rownames(res), res, check.names = FALSE),
          file.path(out_dir, "c2_result.csv"), row.names = FALSE)

feats <- read.csv(file.path(find.package("CytoTRACE2"), "extdata",
                            "features_model_training_17.csv"))
n_mapped <- length(intersect(rownames(mat), as.character(feats[[2]])))
cat(sprintf('{"n_genes_mapped": %d, "n_genes_input": %d}\n',
            n_mapped, nrow(mat)),
    file = file.path(out_dir, "c2_meta.json"))
cat("C2_DONE", nrow(res), "cells\n")
