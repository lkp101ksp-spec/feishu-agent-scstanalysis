# scTenifoldKnk 虚拟敲除批处理（Phase 37）。
# argv: input_csv out_dir gko n_net n_cells min_lib_size mt_threshold
# 输入 genes x cells counts CSV（Python 侧已做 HVG 与子集）；
# 输出 out_dir/diffRegulation.csv。
args <- commandArgs(trailingOnly = TRUE)
input_csv <- args[1]
out_dir <- args[2]
gko <- args[3]
n_net <- as.integer(args[4])
n_cells <- as.integer(args[5])
min_lib <- as.numeric(args[6])
mt_thr <- as.numeric(args[7])

suppressMessages(library(scTenifoldKnk))

mat <- as.matrix(read.csv(input_csv, row.names = 1, check.names = FALSE))
storage.mode(mat) <- "numeric"

set.seed(42)
res <- scTenifoldKnk(
  countMatrix = mat,
  gKO = gko,
  # CRAN 1.1 实测签名：qc_maxMTratio / qc_minLibSize（计划草案参数名有误）
  qc_maxMTratio = mt_thr,
  qc_minLibSize = min_lib,
  nc_nNet = n_net,
  nc_nCells = n_cells,
  nc_nComp = 3,
  nc_scaleScores = TRUE,
  nc_symmetric = FALSE,
  nc_q = 0.9,
  td_K = 3,
  td_maxIter = 1000,
  td_maxError = 1e-5,
  td_nDecimal = 2,
  ma_nDim = 2
)

dr <- res$diffRegulation
write.csv(dr, file.path(out_dir, "diffRegulation.csv"), row.names = FALSE)
cat("KOK_DONE", nrow(dr), "\n")
