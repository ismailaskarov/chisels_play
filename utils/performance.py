from contextlib import contextmanager
from time import perf_counter


def timed(label, func, *args, **kwargs):
    """Run func and print elapsed wall time to the Streamlit/server terminal."""
    start = perf_counter()
    try:
        return func(*args, **kwargs)
    finally:
        elapsed = perf_counter() - start
        print(f"[TIME] {label}: {elapsed:.3f}s")


@contextmanager
def timer(label):
    """Context manager for timing code blocks without changing their behavior."""
    start = perf_counter()
    try:
        yield
    finally:
        elapsed = perf_counter() - start
        print(f"[TIME] {label}: {elapsed:.3f}s")
