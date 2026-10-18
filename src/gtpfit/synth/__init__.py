"""gtpfit.synth — synthetic warehouse datasets with a known answer key."""
from .config import Profile, DefectRates, load_profile
from .generate import generate

__all__ = ["Profile", "DefectRates", "load_profile", "generate"]
