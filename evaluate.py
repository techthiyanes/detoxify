from pytorch_lightning import Trainer, seed_everything

from train import BERTClassifier
from src.utils import ignore_none_collate
import src.data_loaders as module_data
import argparse
import torch
from torch.utils.data import DataLoader
import json
from tqdm import tqdm
from src.utils import move_to
import torch.nn.functional as F
from sklearn.metrics import roc_auc_score
import numpy as np
import os
from src.data_loaders import (
    JigsawData,
    JigsawDataBERT,
    JigsawDataBiasBERT,
    JigsawDataMultilingualBERT,
)
import pandas as pd


def test_classifier(config, dataset, checkpoint_path, device="cuda:1"):

    model = BERTClassifier(config)
    checkpoint = torch.load(checkpoint_path, map_location=device)
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    model.to(device)

    def get_instance(module, name, config, *args, **kwargs):
        return getattr(module, config[name]["type"])(
            *args, **config[name]["args"], **kwargs
        )

    config["dataset"]["args"]["test_csv_file"] = dataset

    test_dataset = get_instance(module_data, "dataset", config, train=False)

    test_data_loader = DataLoader(
        test_dataset,
        batch_size=int(config["batch_size"]),
        num_workers=20,
        shuffle=False,
        collate_fn=ignore_none_collate,
    )

    scores = []
    targets = []
    ids = []
    for *items, meta in tqdm(test_data_loader):
        items = move_to(items, device)
        targets += meta["multi_target"]
        ids += meta["text_id"]
        with torch.no_grad():
            out = model.forward(*items)
            sm = F.sigmoid(out).cpu().detach().numpy()
        scores.extend(sm)

    binary_scores = [s >= 0.5 for s in scores]
    binary_scores = np.stack(binary_scores)
    scores = np.stack(scores)
    targets = np.stack(targets)
    auc_scores = []

    for class_idx in range(scores.shape[1]):
        mask = targets[:, class_idx] != -1
        target_binary = targets[mask, class_idx]
        class_scores = scores[mask, class_idx]
        try:
            auc = roc_auc_score(target_binary, class_scores)
            auc_scores.append(auc)
        except Exception:
            Warning(
                "Only one class present in y_true. ROC AUC score is not defined in that case. Set to nan for now."
            )
            auc_scores.append(np.nan)

    mean_auc = np.mean(auc_scores)

    results = {
        "scores": scores.tolist(),
        "targets": targets.tolist(),
        "auc_scores": auc_scores,
        "mean_auc": mean_auc,
        "ids": ids,
    }

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PyTorch Template")
    parser.add_argument(
        "-c",
        "--config",
        default=None,
        type=str,
        help="config file path (default: None)",
    )
    parser.add_argument(
        "-ckpt",
        "--checkpoint",
        type=str,
        help="path to a saved checkpoint",
    )
    parser.add_argument(
        "-d",
        "--device",
        default=None,
        type=str,
        help="path to a saved checkpoint",
    )
    parser.add_argument(
        "-t",
        "--test_csv",
        default=None,
        type=str,
        help="path to a saved checkpoint",
    )

    args = parser.parse_args()
    config = json.load(open(args.config))

    if args.device is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = args.device
        config["gpus"] = args.device

    results = test_classifier(config, args.test_csv, args.checkpoint)

    with open(args.checkpoint[:-4] + "results.json", "w") as f:
        json.dump(results, f)
    submission = [
        [results["ids"][s], *results["scores"][s]]
        for s in range(len(results["scores"]))
    ]

    df_submission = pd.DataFrame(
        submission,
        columns=[
            "id",
            "toxic",
            "severe_toxic",
            "obscene",
            "threat",
            "insult",
            "identity_hate",
        ],
    )
    df_submission.to_csv(args.checkpoint[:-4] + "submission.csv", index=False)