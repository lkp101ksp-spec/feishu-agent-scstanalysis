# slingshot 谱系推断桥接（sc_pseudotime engine="slingshot"，spec
# 2026-09-13-sc-slingshot-engine-design.md §3）。
# argv: reduced_csv clusters_csv out_dir start_clus
# 输入：reduced_csv = cell,UMAP1,UMAP2；clusters_csv = cell,leiden
# （Python 侧已对齐同一细胞顺序）；start_clus 空串 = 自由推根。
# 输出：out_dir/sling_pst.csv（cell × lineage 主曲线拟时序宽表）、
#       out_dir/sling_curves.csv（lineage,ord,UMAP1,UMAP2 曲线折点）。
args <- commandArgs(trailingOnly = TRUE)
reduced_csv <- args[1]
clusters_csv <- args[2]
out_dir <- args[3]
start_clus <- args[4]

suppressMessages(library(slingshot))

rd <- read.csv(reduced_csv, row.names = 1, check.names = FALSE)
cl <- read.csv(clusters_csv, row.names = 1, check.names = FALSE)
cells <- intersect(rownames(rd), rownames(cl))
rd <- as.matrix(rd[cells, , drop = FALSE])
cl <- as.character(cl[cells, 1])

sds <- if (nzchar(start_clus)) {
  if (!start_clus %in% cl) {
    stop(sprintf("start_clus %s not in clusters", start_clus))
  }
  slingshot(rd, cl, start.clus = start_clus)
} else {
  slingshot(rd, cl)
}

pst <- slingPseudotime(sds)
colnames(pst) <- paste0("lineage", seq_len(ncol(pst)))
write.csv(data.frame(cell = rownames(pst), pst, check.names = FALSE),
          file.path(out_dir, "sling_pst.csv"), row.names = FALSE)

curves <- slingCurves(sds)
rows <- lapply(seq_along(curves), function(i) {
  s <- curves[[i]]$s[curves[[i]]$ord, , drop = FALSE]
  data.frame(lineage = paste0("lineage", i), ord = seq_len(nrow(s)),
             UMAP1 = s[, 1], UMAP2 = s[, 2])
})
write.csv(do.call(rbind, rows),
          file.path(out_dir, "sling_curves.csv"), row.names = FALSE)
cat("SLING_DONE", ncol(pst), "\n")
