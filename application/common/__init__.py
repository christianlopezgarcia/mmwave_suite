"""Shared plumbing for applications: the AppModule contract and its drivers."""

from .app import AppModule, iter_dat, run_live, run_offline

__all__ = ["AppModule", "iter_dat", "run_live", "run_offline"]
