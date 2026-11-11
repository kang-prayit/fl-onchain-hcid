"""MedMNIST+ v3.0 loader (Zenodo record 10519652, CC BY 4.0) and IID / Dirichlet partitioning."""
from __future__ import annotations
import hashlib, os, urllib.request
import numpy as np

ZENODO = "https://zenodo.org/records/10519652/files/{name}?download=1"
MD5 = {  # from the Zenodo API, record 10519652 (checked 10 Nov 2026)
    "pneumoniamnist.npz": "28209eda62fecd6e6a2d98b1501bb15f",
    "pathmnist.npz": "a8b06965200029087d5bd730944a56c1",
}

# First level matches the original implementation (1000 draws at min_size=10).
# Lower levels are only used when that is infeasible; they never change a
# configuration that already succeeded. min_size is never lowered to 0.
DEFAULT_MIN_SIZE = 10
DEFAULT_ATTEMPTS_FIRST = 1000
DEFAULT_ATTEMPTS_RELAXED = 10000
DEFAULT_MIN_SIZE_LADDER = (10, 5, 2)


def md5sum(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def ensure_dataset(name: str, data_dir: str) -> str:
    """Download <name>.npz once and verify its md5."""
    fn = name if name.endswith(".npz") else f"{name}.npz"
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, fn)
    if not os.path.exists(path):
        tmp = path + ".part"
        urllib.request.urlretrieve(ZENODO.format(name=fn), tmp)
        os.replace(tmp, path)
    got = md5sum(path)
    if fn in MD5 and got != MD5[fn]:
        raise RuntimeError(f"md5 mismatch for {fn}: {got} != {MD5[fn]}")
    return path


def load_npz(path: str):
    """Returns dict with x_train,y_train,x_val,y_val,x_test,y_test as **uint8** NCHW (scaled to [0,1] per batch
    in model.py, to keep PathMNIST at ~0.2 GB per process) and int64 labels."""
    z = np.load(path)
    out = {}
    for s in ("train", "val", "test"):
        x = z[f"{s}_images"]
        if x.ndim == 3:  # grayscale N,H,W
            x = x[:, None, :, :]
        else:            # N,H,W,C -> N,C,H,W
            x = x.transpose(0, 3, 1, 2)
        out[f"x_{s}"] = np.ascontiguousarray(x)
        out[f"y_{s}"] = z[f"{s}_labels"].reshape(-1).astype(np.int64)
    out["n_classes"] = int(max(out["y_train"].max(), out["y_test"].max()) + 1)
    out["in_ch"] = int(out["x_train"].shape[1])
    return out


def partition_iid(n: int, k: int, seed: int) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    return [np.sort(p) for p in np.array_split(idx, k)]


def _client_stats(labels: np.ndarray, parts: list[np.ndarray]) -> list[dict]:
    classes = [int(c) for c in np.unique(labels)]
    rows = []
    for i, p in enumerate(parts):
        y = labels[p]
        counts = {c: int(np.sum(y == c)) for c in classes}
        rows.append({
            "client": int(i),
            "n": int(len(p)),
            "n_labels": int(sum(1 for v in counts.values() if v > 0)),
            "label_counts": counts,
        })
    return rows


def _sizes_meta(stats: list[dict]) -> dict:
    sizes = [s["n"] for s in stats]
    return dict(
        client_min=int(min(sizes)) if sizes else 0,
        client_max=int(max(sizes)) if sizes else 0,
        client_mean=float(np.mean(sizes)) if sizes else 0.0,
        client_sizes=sizes,
        n_empty=int(sum(1 for s in sizes if s == 0)),
        clients=stats,
    )


def _min_size_ladder(requested: int, ladder: tuple[int, ...] | None) -> list[int]:
    if requested <= 0:
        raise ValueError("min_size must be >= 1 (never 0)")
    steps = DEFAULT_MIN_SIZE_LADDER if ladder is None else tuple(ladder)
    out: list[int] = []
    for s in (requested,) + tuple(steps):
        if s <= 0:
            raise ValueError("min_size ladder values must be >= 1 (never 0)")
        if s <= requested and s not in out:
            out.append(int(s))
    if not out:
        raise ValueError("empty min_size ladder")
    return out


def partition_dirichlet(labels: np.ndarray, k: int, alpha: float, seed: int, min_size: int = DEFAULT_MIN_SIZE,
                        max_attempts: int = DEFAULT_ATTEMPTS_FIRST,
                        max_attempts_relaxed: int = DEFAULT_ATTEMPTS_RELAXED,
                        min_size_ladder: tuple[int, ...] | None = None) -> list[np.ndarray]:
    """Label-skew Dirichlet partition (Hsu et al. 2019 style).

    The first ``max_attempts`` draws use the requested ``min_size`` (default 10)
    and the same RNG stream as the original implementation, so any configuration
    that already succeeded is bit-identical. If that is infeasible, the same RNG
    continues down the ladder (5, then 2; never 0) for ``max_attempts_relaxed``
    draws at each lower level. Metadata is stored on ``partition_dirichlet.last_meta``.
    """
    ladder = _min_size_ladder(min_size, min_size_ladder)
    rng = np.random.default_rng(seed)
    classes = np.unique(labels)
    attempts_per_level: dict[int, int] = {int(lv): 0 for lv in ladder}
    total_attempts = 0
    last_min_obs = 0

    def _meta(feasible: bool, level, parts=None, reason=None) -> dict:
        base = dict(
            scheme="dirichlet",
            alpha=float(alpha),
            k=int(k),
            seed=int(seed),
            n_train=int(len(labels)),
            n_classes=int(len(classes)),
            requested_min_size=int(min_size),
            effective_min_size=int(level) if level is not None else None,
            min_size_ladder=[int(x) for x in ladder],
            max_attempts_first=int(max_attempts),
            max_attempts_relaxed=int(max_attempts_relaxed),
            attempts=int(total_attempts),
            attempts_per_level={str(lv): int(n) for lv, n in attempts_per_level.items()},
            last_min_observed=int(last_min_obs),
            feasible=bool(feasible),
        )
        if parts is not None:
            base.update(_sizes_meta(_client_stats(labels, parts)))
        if reason is not None:
            base["reason"] = reason
        return base

    for i, level in enumerate(ladder):
        natt = max_attempts if i == 0 else max_attempts_relaxed
        for _ in range(natt):
            total_attempts += 1
            attempts_per_level[int(level)] += 1
            parts_raw = [[] for _ in range(k)]
            for c in classes:
                idc = rng.permutation(np.where(labels == c)[0])
                p = rng.dirichlet(np.full(k, alpha))
                cuts = (np.cumsum(p) * len(idc)).astype(int)[:-1]
                for j, chunk in enumerate(np.split(idc, cuts)):
                    parts_raw[j].extend(chunk.tolist())
            last_min_obs = min(len(p) for p in parts_raw)
            if last_min_obs >= level:
                parts = [np.sort(np.array(p, dtype=np.int64)) for p in parts_raw]
                partition_dirichlet.last_meta = _meta(True, level, parts)
                return parts

    reason = (f"Dirichlet partition failed to satisfy min_size even at {ladder[-1]} "
              f"after {total_attempts} attempts (ladder={ladder}, last_min_observed={last_min_obs})")
    partition_dirichlet.last_meta = _meta(False, None, reason=reason)
    raise RuntimeError(reason)


partition_dirichlet.last_meta = None


def make_partitions(labels: np.ndarray, k: int, scheme: str, alpha: float | None, seed: int):
    if scheme == "iid":
        parts = partition_iid(len(labels), k, seed)
        meta = dict(
            scheme="iid",
            alpha=None,
            k=int(k),
            seed=int(seed),
            n_train=int(len(labels)),
            n_classes=int(len(np.unique(labels))),
            requested_min_size=None,
            effective_min_size=None,
            min_size_ladder=None,
            attempts=1,
            attempts_per_level=None,
            feasible=True,
        )
        meta.update(_sizes_meta(_client_stats(labels, parts)))
        make_partitions.last_meta = meta
        return parts
    if scheme == "dirichlet":
        try:
            parts = partition_dirichlet(labels, k, float(alpha), seed)
        except RuntimeError:
            make_partitions.last_meta = partition_dirichlet.last_meta
            raise
        make_partitions.last_meta = partition_dirichlet.last_meta
        return parts
    raise ValueError(scheme)


make_partitions.last_meta = None
