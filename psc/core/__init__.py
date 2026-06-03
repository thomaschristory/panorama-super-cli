"""Backend service layer — framework-free.

Nothing in `psc.core` imports from `psc.cli` or any UI framework. The CLI
(`psc.cli`) is one frontend; a future web API would be another, importing the
same engines (`resolve`, `dedup`, `naming`, `refs`, `changeset`) and models.
"""
