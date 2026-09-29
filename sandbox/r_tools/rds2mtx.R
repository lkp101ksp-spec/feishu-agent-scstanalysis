# rds → mtx 桥接（sc_load .rds 支持，2026-09-29 PDAC 事故修复②）。
# argv: rds_path out_dir
# 输入：.rds 文件（Seurat v4/v5 对象；SingleCellExperiment 尽力支持）
# 输出：out_dir/matrix.mtx（MatrixMarket 稀疏 counts）、barcodes.tsv、
#       features.tsv、obs.csv（Seurat meta.data / SCE colData，
#       首列 cell barcode，与 matrix 列序一致）。
# 设计取舍：不走 zellkonverter——其 basilisk 运行期拉 conda 环境，容器
# 断网即死；mtx+CSV 纯文本桥与既有 *_bridge.R 惯例一致、零新增依赖
# （Matrix/Seurat 已由 CellChat dependencies=TRUE 带入镜像）。
args <- commandArgs(trailingOnly = TRUE)
rds_path <- args[1]
out_dir <- args[2]

suppressMessages(library(Matrix))
dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)

obj <- readRDS(rds_path)
cls <- class(obj)[1]

extract_seurat <- function(o) {
  suppressMessages(library(SeuratObject))
  assay <- DefaultAssay(o)
  # v5 先 layer 后 slot（v5.1 改名；slot 在 v4/v5.0 有效）；分片
  # counts.* 需 JoinLayers 合并（真机 GSE278688/9 即 v5 分片）。
  counts <- tryCatch(GetAssayData(o, assay = assay, layer = "counts"),
                     error = function(e) NULL)
  if (is.null(counts) || nrow(counts) == 0) {
    counts <- tryCatch(GetAssayData(o, assay = assay, slot = "counts"),
                       error = function(e) NULL)
  }
  if (is.null(counts) || nrow(counts) == 0) {
    o <- tryCatch(JoinLayers(o, assay = assay), error = function(e) o)
    counts <- tryCatch(GetAssayData(o, assay = assay, layer = "counts"),
                       error = function(e) NULL)
  }
  if (is.null(counts) || nrow(counts) == 0) {
    stop("Seurat object has no counts layer (data/scale.data only 不支持——",
         "sc_load 需要原始 counts)")
  }
  list(counts = counts, meta = o[[]])
}

extract_sce <- function(o) {
  if (!requireNamespace("SingleCellExperiment", quietly = TRUE)) {
    stop("rds 为 SingleCellExperiment 但镜像缺 SingleCellExperiment 包")
  }
  cn <- SummarizedExperiment::assayNames(o)
  use <- if ("counts" %in% cn) "counts" else cn[1]
  if (use != "counts") {
    warning("SCE 无 counts assay，退用第一个 assay: ", use)
  }
  list(counts = SummarizedExperiment::assay(o, use),
       meta = as.data.frame(SummarizedExperiment::colData(o)))
}

parts <- if (inherits(obj, "Seurat")) {
  extract_seurat(obj)
} else if (inherits(obj, "SingleCellExperiment") ||
           inherits(obj, "SummarizedExperiment")) {
  extract_sce(obj)
} else {
  stop("unsupported rds object class: ", cls,
       "（支持 Seurat / SingleCellExperiment；其它请先转 h5ad）")
}

counts <- as(parts$counts, "CsparseMatrix")
cells <- colnames(counts)
genes <- rownames(counts)
if (is.null(cells) || is.null(genes)) {
  stop("counts 缺 dimnames（细胞/基因名），无法桥接")
}

meta <- parts$meta
# meta 行名与 counts 列对齐（Seurat meta 行序即细胞序；防御性重排）
if (!is.null(rownames(meta)) && all(cells %in% rownames(meta))) {
  meta <- meta[cells, , drop = FALSE]
}
# data.frame 列里嵌 list（Seurat 常见）直接写 csv 会炸——逐列拍平成字符
for (nm in names(meta)) {
  if (is.list(meta[[nm]]) && !is.data.frame(meta[[nm]])) {
    meta[[nm]] <- vapply(meta[[nm]],
                         function(x) paste(as.character(x), collapse = ";"),
                         character(1))
  }
}

writeMM(counts, file.path(out_dir, "matrix.mtx"))
writeLines(cells, file.path(out_dir, "barcodes.tsv"))
writeLines(genes, file.path(out_dir, "features.tsv"))
write.csv(data.frame(cell = cells, meta, check.names = FALSE),
          file.path(out_dir, "obs.csv"), row.names = FALSE)

cat(sprintf("rds2mtx ok: class=%s cells=%d genes=%d\n",
            cls, length(cells), length(genes)))
