import os
from copy import deepcopy
from datetime import datetime
from typing import Callable

import torch
from sklearn.model_selection import StratifiedKFold
from torch.utils.data import Subset, DataLoader
from tqdm.notebook import tqdm, trange

from torchsampler import ImbalancedDatasetSampler


device = "cuda:0" if torch.cuda.is_available() else "cpu"


def train(dataloader, model, criterion, optimizer, progressbar: tqdm):
    size = len(dataloader.dataset)
    num_batches = len(dataloader)
    progressbar.set_description(desc="train")
    train_loss = 0

    for batch, (ids, mask, tti, label) in enumerate(dataloader):
        ids, mask, tti, label = (
            ids.to(device),
            mask.to(device),
            tti.to(device),
            label.to(device),
        )

        pred = model(input_ids=ids, attention_mask=mask, token_type_ids=tti)
        logits = pred.logits.to(device)
        loss = criterion(logits.view(-1, 2), label.view(-1))

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        train_loss += loss.item()
        if batch % 10 == 0:
            loss_, current = loss.item(), batch * len(ids)
            # print(f"loss: {loss_:>7f} [{current:>5d}/{size:>5d}]")
            progressbar.set_postfix(train_loss=loss_)

        progressbar.update()

    train_loss /= num_batches
    return train_loss


def test(dataloader, model, criterion, progressbar: tqdm):
    size = len(dataloader.dataset)
    num_batches = len(dataloader)
    progressbar.set_description(desc="test")

    model.eval()
    test_loss, correct = 0, 0

    with torch.no_grad():
        for ids, mask, tti, label in dataloader:
            ids, mask, tti, label = (
                ids.to(device),
                mask.to(device),
                tti.to(device),
                label.to(device),
            )

            pred = model(input_ids=ids, attention_mask=mask, token_type_ids=tti)
            logits = pred.logits.to(device)
            loss = criterion(logits.view(-1, 2), label.view(-1))

            test_loss += loss
            correct += (pred.logits.argmax(1) == label).type(torch.float).sum().item()

            progressbar.update()

    test_loss /= num_batches
    correct /= size
    # print(f"Test Error: Accuracy: {100 * correct:>0.1f}%, Avg loss: {test_loss:>8f}\n")

    return correct, test_loss


def training(
    dataset: torch.utils.data.Dataset,
    model: torch.nn.Module,
    criterion,
    optimizer,
    scheduler=None,
    get_label: Callable = None,
    epochs: int = 100,
    cv: int = 5,
    batch_size: int = 64,
    save_path: str = "",
):

    # ????????? ???????????? ????????? ??????????????? ????????? ??????
    best_score = 0

    # ?????? ?????? ?????????: checkpoint{????????????}.pth, ???) checkpoint210813.pth
    t = datetime.now()
    today = f"{t.year % 100}{t.month:02}{t.day:02}"
    file_name = f"checkpoint{today}.pth"
    path_and_file_name = os.path.join(save_path, file_name)

    for epoch in trange(1, epochs + 1, desc="Epoch"):
        print(f"------------------------- Epoch {epoch:>3} -------------------------")
        kfold = StratifiedKFold(n_splits=cv, shuffle=True, random_state=42)

        # progressbar ??????
        size = len(dataset)
        tqdm_size = -(-size // batch_size) * cv  # math.floor??? ?????? ????????? ?????? ??????
        progressbar = tqdm(total=tqdm_size, leave=False)

        # epoch?????? ????????? ?????? ??????
        avg_train_loss = 0
        avg_test_loss = 0
        avg_accuracy = 0

        # KFold ??????
        for train_idx, val_idx in kfold.split(dataset[:][0], dataset[:][3]):
            # fold??? ??????????????? ??????
            train_data = Subset(dataset, train_idx)
            val_data = Subset(dataset, val_idx)

            train_loader = DataLoader(
                train_data,
                batch_size=batch_size,
                sampler=ImbalancedDatasetSampler(
                    train_data, callback_get_label=get_label
                ),
            )
            val_loader = DataLoader(
                val_data,
                batch_size=batch_size,
                sampler=ImbalancedDatasetSampler(
                    val_data, callback_get_label=get_label
                ),
            )

            # train, test ?????? ??????
            train_loss = train(train_loader, model, criterion, optimizer, progressbar)
            accuracy, test_loss = test(val_loader, model, criterion, progressbar)

            # epoch?????? ????????? ?????? ??????
            avg_train_loss += train_loss
            avg_test_loss += test_loss
            avg_accuracy += accuracy
        # KFold ???
        progressbar.close()  # ????????? ??????, leave=False??? ?????????????????? ??? ??? ?????????

        if scheduler is not None:
            scheduler.step()

        # epoch ?????? ??????
        avg_train_loss /= cv
        avg_test_loss /= cv
        avg_accuracy /= cv

        print(
            f"train_loss: {avg_train_loss:.6f} | test_loss: {avg_test_loss:.6f} | accuracy: {avg_accuracy:.4f}"
        )

        # ????????? ?????? ??????
        if best_score <= avg_accuracy:
            best_score = avg_accuracy

            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "optimizer_state_dict": optimizer.state_dict(),
                    "scheduler_state_dict": scheduler.state_dict(),
                },
                path_and_file_name,
            )
            print(f"?????? ??????: {path_and_file_name}, accuarcy: {best_score}")

    # ?????? ??????
    print("??????")

    # ????????? ?????? ??????
    file_name_final = f"model{today}-final.pth"
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "scheduler_state_dict": scheduler.state_dict(),
        },
        os.path.join(save_path, file_name_final),
    )
