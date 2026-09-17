# CytoTRACE2 绝对干性打分桥接（sc_cytotrace2，测试总结六补记死刑
# 推翻翻案落地）。
# argv: expr_tsv out_dir species ncores batch_size smooth_batch_size
# 输入：expr_tsv = 基因×细胞 tab 表（首列基因名、首行细胞条码，
#   Python 侧已导出为 raw/CPM 整数计数——模型要求不能 log/scaled）。
# 输出：out_dir/c2_result.csv（cell + CytoTRACE2_Score/Potency/
#   Relative/preKNN_Score/preKNN_Potency 五列）+
#   out_dir/c2_meta.json（n_genes_mapped/n_genes_input 供上游上报；
#   映射口径与包内 preprocessData 同源 intersect——特征集 csv 基因名
#   在第二列且首行伪表头 `,0`，六补记探针钉注）。
# 模型参数 parameter_dict_19.rds 等 5 件随包 inst/extdata 分发，
# 断网可用（容器 --network none 实跑实证）。
suppressMessages(library(CytoTRACE2))
suppressMessages(library(data.table))

args <- commandArgs(trailingOnly = TRUE)
expr_tsv <- args[1]
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

dt <- fread(expr_tsv)
mat <- as.matrix(dt[, -1])
rownames(mat) <- as.character(dt[[1]])
storage.mode(mat) <- "double"

call_args <- list(input = mat, species = species, ncores = ncores)
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
