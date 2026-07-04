from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("research-git")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"
