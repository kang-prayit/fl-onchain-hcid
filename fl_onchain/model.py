"""Small CNN used only as a sanity-check workload (accuracy is not the paper's claim)."""
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


class SmallCNN(nn.Module):
    def __init__(self, in_ch: int = 1, n_classes: int = 2, width: int = 16):
        super().__init__()
        self.c1 = nn.Conv2d(in_ch, width, 3, padding=1)
        self.c2 = nn.Conv2d(width, 2 * width, 3, padding=1)
        self.fc1 = nn.Linear(2 * width * 7 * 7, 64)
        self.fc2 = nn.Linear(64, n_classes)

    def forward(self, x):
        x = F.max_pool2d(F.relu(self.c1(x)), 2)
        x = F.max_pool2d(F.relu(self.c2(x)), 2)
        x = F.relu(self.fc1(x.flatten(1)))
        return self.fc2(x)


def get_state(model) -> dict[str, np.ndarray]:
    return {k: v.detach().cpu().numpy().astype(np.float32) for k, v in model.state_dict().items()}


def set_state(model, state: dict[str, np.ndarray]):
    model.load_state_dict({k: torch.from_numpy(np.asarray(v)) for k, v in state.items()})


def _f(xb):
    return xb.float().div_(255.0) if xb.dtype == torch.uint8 else xb


def train_local(model, x, y, epochs=1, bs=64, lr=1e-3, seed=0):
    g = torch.Generator().manual_seed(seed)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    xt, yt = torch.from_numpy(np.ascontiguousarray(x)), torch.from_numpy(y)
    model.train()
    for _ in range(epochs):
        perm = torch.randperm(len(xt), generator=g)
        for i in range(0, len(perm), bs):
            b = perm[i:i + bs]
            opt.zero_grad()
            F.cross_entropy(model(_f(xt[b])), yt[b]).backward()
            opt.step()


@torch.no_grad()
def evaluate(model, x, y, bs=512):
    model.eval()
    correct, loss = 0, 0.0
    for i in range(0, len(x), bs):
        xb, yb = _f(torch.from_numpy(np.ascontiguousarray(x[i:i + bs]))), torch.from_numpy(y[i:i + bs])
        out = model(xb)
        loss += F.cross_entropy(out, yb, reduction="sum").item()
        correct += (out.argmax(1) == yb).sum().item()
    return {"accuracy": correct / len(x), "loss": loss / len(x)}


def state_to_bytes(state: dict[str, np.ndarray]) -> bytes:
    """Canonical serialisation of an update: float32 little-endian, keys in sorted order."""
    return b"".join(np.ascontiguousarray(state[k], dtype="<f4").tobytes() for k in sorted(state))
