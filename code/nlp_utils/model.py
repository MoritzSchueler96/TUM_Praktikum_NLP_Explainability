from numpy import average
import torch
from torch import nn
import torch.nn.functional as F
import pytorch_lightning as pl
from transformers import DistilBertModel, BertModel
from nlp_utils.config import create_config
import torchmetrics


class BaseModel(pl.LightningModule):
    """
    This is the DistilBERT based model. It's called "BaseModel" as I thought it would
    be the only NN model we would make...
    """

    def __init__(self, config={}):

        super().__init__()
        config = create_config(config)
        self.config = config
        self.save_hyperparameters(self.config)
        self.learning_rate = config["learning_rate"]

        self.train_metric = torchmetrics.MeanSquaredError()
        self.val_metric = torchmetrics.MeanSquaredError()
        self.test_metric = torchmetrics.MeanSquaredError()

        # setup layers
        self.bert = DistilBertModel.from_pretrained("distilbert-base-uncased")

        if self.config["vocab_size"] == 0:
            self.config["vocab_size"] = self.bert.config.vocab_size

        # freeze the encode, head layer will still be trainable
        for param in self.bert.parameters():
            param.requires_grad = False

        # Model design
        self.distilbert_tail = nn.Sequential(
            nn.Linear(self.bert.config.dim, self.bert.config.dim),
            nn.ReLU(),
            nn.Dropout(self.bert.config.seq_classif_dropout),
        )

        self.category_encoder = nn.Sequential(
            nn.Linear(
                config["category_encoded_length"], config["category_encoder_out"]
            ),
            nn.ReLU(),
        )
        # 768 bert hidden state shape + category_encoder_out
        self.classifier = nn.Linear(
            self.bert.config.hidden_size + config["category_encoder_out"], 1
        )

    def forward(self, encoded_text, category_vectors):
        bert_output = self.bert(
            encoded_text["input_ids"], encoded_text["attention_mask"]
        )

        hidden_state = bert_output[0]  # (bs, seq_len, dim)
        pooled_output = hidden_state[:, 0]  # (bs, dim)
        pooled_output = self.distilbert_tail(pooled_output)

        categories_encoded = self.category_encoder(category_vectors)
        # test = torch.cat((pooled_output, categories_encoded))
        concat = torch.cat((pooled_output, categories_encoded), 1)

        out = self.classifier(concat)
        # out = self.output_layer(bert_output['pooler_output'])
        return out

    def training_step(self, batch, batch_idx):
        y, encoded_texts, category_vectors, _ = batch
        y, encoded_texts, category_vectors = (
            y.to(self.device),
            encoded_texts.to(self.device),
            category_vectors.to(self.device),
        )

        y_hat = self(encoded_texts, category_vectors)

        loss = F.mse_loss(y_hat.view(-1), y.view(-1))
        self.train_metric(y_hat, y.unsqueeze(1))

        self.log(
            "train_loss", loss, on_step=True, on_epoch=True, prog_bar=True, logger=True
        )
        return {"loss": loss}

    def training_epoch_end(self, outs):
        self.log(
            "train_epoch" + type(self.train_metric).__name__,
            self.train_metric.compute(),
        )

    def validation_step(self, batch, batch_idx):
        y, encoded_texts, category_vectors, _ = batch
        y, encoded_texts, category_vectors = (
            y.to(self.device),
            encoded_texts.to(self.device),
            category_vectors.to(self.device),
        )

        y_hat = self(encoded_texts, category_vectors)

        loss = F.mse_loss(y_hat, y.unsqueeze(1))
        self.val_metric(y_hat, y.unsqueeze(1))

        self.log("val_loss", loss)
        return {"val_loss": loss}

    def validation_epoch_end(self, outputs):
        self.log(
            "val_epoch_" + type(self.train_metric).__name__, self.val_metric.compute()
        )

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.parameters(), lr=self.learning_rate)
        return optimizer


class BiLSTMModel(pl.LightningModule):
    """
    This is the second neural model. (BiLSTM)
    """

    def __init__(self, config={}):
        super().__init__()

        config = create_config(config)
        self.config = config
        self.save_hyperparameters(self.config)
        self.learning_rate = config["learning_rate"]

        self.train_metric = pl.metrics.MeanSquaredError()
        self.val_metric = pl.metrics.MeanSquaredError()

        # Network structure
        self.embedding = nn.Embedding(config["vocab_size"], config["embedding_dim"])

        self.category_encoder = nn.Sequential(
            nn.Linear(
                config["category_encoded_length"], config["category_encoder_out"]
            ),
            nn.ReLU(),
        )

        self.bilstm = nn.LSTM(
            input_size=config["embedding_dim"],
            hidden_size=config["bilstm_hidden_dim"],
            bidirectional=True,
            batch_first=True,
        )

        self.classifier = nn.Linear(
            config["bilstm_hidden_dim"] * 2 + config["category_encoder_out"], 1
        )  #

    def forward(self, encoded_texts, encoded_classes):
        embeddings = self.embedding(encoded_texts)
        lstm_out, _ = self.bilstm(embeddings)
        categories_encoded = self.category_encoder(encoded_classes)
        # print("categories_encoded", categories_encoded.shape)
        # concat = torch.cat((lstm_out, categories_encoded),1)
        forward = lstm_out[:, lstm_out.shape[1] - 1, :]
        # backward = lstm_out[:, 0, :]
        combined = torch.cat((forward, categories_encoded), 1)
        out = self.classifier(combined)
        return out

    def training_step(self, batch, batch_idx):
        y, encoded_texts, encoded_classes, _ = batch
        y, encoded_texts, encoded_classes = (
            y.to(self.device),
            encoded_texts.to(self.device),
            encoded_classes.to(self.device),
        )

        y_hat = self(encoded_texts, encoded_classes)

        loss = F.mse_loss(y_hat, y.unsqueeze(1))
        self.train_metric(y_hat, y.unsqueeze(1))

        self.log("loss", loss)
        return {"loss": loss}

    def training_epoch_end(self, outs):
        self.log(
            "train_epoch" + type(self.train_metric).__name__,
            self.train_metric.compute(),
        )

    def validation_step(self, batch, batch_idx):
        y, encoded_texts, encoded_classes, _ = batch
        y, encoded_texts, encoded_classes = (
            y.to(self.device),
            encoded_texts.to(self.device),
            encoded_classes.to(self.device),
        )

        y_hat = self(encoded_texts, encoded_classes)

        loss = F.mse_loss(y_hat, y.unsqueeze(1))
        self.val_metric(y_hat, y.unsqueeze(1))

        self.log("val_loss", loss)
        return {"val_loss": loss}

    def validation_epoch_end(self, outputs):
        self.log(
            "val_epoch_" + type(self.train_metric).__name__, self.val_metric.compute()
        )

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.parameters(), lr=self.learning_rate)
        return optimizer


class CustomDistilBertModel(pl.LightningModule):
    """
    This is the DistilBERT based model for stance prediction
    """

    def __init__(self, config={}):

        super().__init__()
        config = create_config(config)
        self.config = config
        self.save_hyperparameters(self.config)
        self.learning_rate = config["learning_rate"]
        self.num_classes = 4

        self.train_metric = torchmetrics.F1(
            num_classes=self.num_classes, average="macro"
        )
        self.val_metric = torchmetrics.F1(num_classes=self.num_classes, average="macro")
        self.test_metric = torchmetrics.F1(
            num_classes=self.num_classes, average="macro"
        )

        # setup layers
        self.bert = DistilBertModel.from_pretrained("distilbert-base-uncased")

        if self.config["vocab_size"] == 0:
            self.config["vocab_size"] = self.bert.config.vocab_size

        # freeze the encode, head layer will still be trainable
        for param in self.bert.parameters():
            param.requires_grad = False

        # Model design
        self.distilbert_tail = nn.Sequential(
            nn.Linear(self.bert.config.dim, self.bert.config.dim),
            nn.ReLU(),
            nn.Dropout(self.bert.config.seq_classif_dropout),
        )

        # 768 bert hidden state shape + category_encoder_out
        self.classifier = nn.Linear(self.bert.config.hidden_size, self.num_classes,)

    def forward(self, encoded_text):
        with torch.no_grad():
            bert_output = self.bert(
                encoded_text["input_ids"], encoded_text["attention_mask"]
            )

        hidden_state = bert_output[0]  # (bs, seq_len, dim)
        pooled_output = hidden_state[:, 0]  # (bs, dim)
        pooled_output = self.distilbert_tail(pooled_output)
        out = self.classifier(pooled_output)
        return out  # torch.argmax(out, axis=1)

    def training_step(self, batch, batch_idx):
        y, encoded_texts, _ = batch
        y, encoded_texts = (
            y.to(self.device),
            encoded_texts.to(self.device),
        )

        y_hat = self(encoded_texts)
        pred = torch.argmax(y_hat, axis=1)

        loss = F.cross_entropy(y_hat, y)
        self.train_metric(pred, y)

        self.log(
            "train_loss", loss, on_step=True, on_epoch=True, prog_bar=True, logger=True
        )
        return {"loss": loss}

    def training_epoch_end(self, outs):
        self.log(
            "train_epoch_" + type(self.train_metric).__name__,
            self.train_metric.compute(),
        )

    def validation_step(self, batch, batch_idx):
        y, encoded_texts, _ = batch
        y, encoded_texts = (
            y.to(self.device),
            encoded_texts.to(self.device),
        )

        y_hat = self(encoded_texts)
        pred = torch.argmax(y_hat, axis=1)

        loss = F.cross_entropy(y_hat, y)
        self.val_metric(pred, y)

        self.log("val_loss", loss)
        return {"val_loss": loss}

    def validation_epoch_end(self, outputs):
        self.log(
            "val_epoch_" + type(self.val_metric).__name__, self.val_metric.compute()
        )

    def test_step(self, batch, batch_idx):
        y, encoded_texts, _ = batch
        y, encoded_texts = (
            y.to(self.device),
            encoded_texts.to(self.device),
        )

        y_hat = self(encoded_texts)
        pred = torch.argmax(y_hat, axis=1)

        loss = F.cross_entropy(y_hat, y)
        self.test_metric(pred, y)

        self.log("test_loss", loss)
        return {"test_loss": loss}

    def test_epoch_end(self, outputs):
        self.log(
            "test_epoch_" + type(self.test_metric).__name__, self.test_metric.compute()
        )

    def configure_optimizers(self):
        # Make sure to filter the parameters based on `requires_grad`
        """
        return torch.optim.Adam(
            filter(lambda p: p.requires_grad, self.parameters), lr=self.learning_rate
        )
        """
        return torch.optim.Adam(self.parameters(), lr=self.learning_rate)

