# Redmine Ticket Notifier

## 概要
Redmineに登録されたチケットをSlackのチャンネルに通知します。

Alertmanagerから送信されたアラートをRedmineにチケットとして作成する [Redmine-Ticket-Creator](https://github.com/cdsl-research/redmine-ticket-creater) と組み合わせて使います。

※ Slack Appが作成されていることが前提です。

## 機能
- 指定したプロジェクトとトラッカーの新規発行チケットの通知
- 通知メッセージにチケットの担当者のメンションを追加（チケットの担当者名とSlackのメンバーIDの紐付けが必要）
- チケットのステータスが完了に変更されると，該当チケットの通知メッセージに「✅」のリアクションを追加
- 特定のトラッカーにチケットが移動された場合に，該当チケットの通知メッセージに「🗑️」のリアクションを追加

## 構成
```
redmine-ticket-notifier/
├── deploy
│   ├── deployment.yaml
│   ├── pvc.yaml
│   └── secret.yaml.example
├── app.py
├── Dockerfile
└── README.md
```


## 環境構成
- Ubuntu Server 24.04.2 LTS
- K3s v1.30.6+k3s1
    - Master/Worker Node:<br>vCPU: 4 cores, RAM: 8GB, SSD: 40GB, OS: Ubuntu Server 24.04.2 LTS
- Prometheus 2.53.1
- Redmine 6.0.4.stable
- Slack 4.46.101
- Python 3.12.3
    - requests
    - slack-sdk

## セットアップ

### 1. リポジトリをクローンする
```
$ git clone https://github.com/cdsl-research/redmine-ticket-notifier.git
Cloning into 'redmine-ticket-notifier'...
remote: Enumerating objects: 16, done.
remote: Counting objects: 100% (16/16), done.
remote: Compressing objects: 100% (13/13), done.
remote: Total 16 (delta 4), reused 15 (delta 3), pack-reused 0 (from 0)
Receiving objects: 100% (16/16), 12.49 KiB | 6.24 MiB/s, done.
Resolving deltas: 100% (4/4), done.
$

$ cd redmine-ticket-notifier
$ 
```

### 2. 環境変数を設定する

`deploy/deployment.yaml`を編集し、使用する環境に応じて環境変数を設定してください。

- `REDMINE_URL` : RedmineのURL
- `PROJECT_ID` : 新規作成チケットの検出対象であるプロジェクトの識別子
- `TRACKER_ID` : 新規作成チケットの検出対象であるトラッカーの識別子
- `INTERVAL` : チケット確認の周期（秒）


### 3. 必要な認証情報やIDを設定する

```
$ cp deploy/secret.yaml.example deploy/secret.yaml
```

`deploy/secret.yaml`を編集し、必要な認証情報やIDを設定してください。

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: redmine-api-secret
  namespace: redmine
type: Opaque
stringData:
  # RedmineのAPIアクセスキーに置き換える
  apiKey: "<redmine-api-key>"
---
apiVersion: v1
kind: Secret
metadata:
  name: slack-app-secret
  namespace: redmine
type: Opaque
stringData:
  # SlackのBot Tokenを入力する
  botToken: "<slack-bot-token>"
  # 通知対象のチャンネルIDを入力する
  channelId: "<slack-channel-id>"
---
apiVersion: v1
kind: Secret
metadata:
  name: user-mapping-secret
  namespace: redmine
type: Opaque
stringData:
  # Redmine上の担当者名とSlackのメンバーIDの対応を辞書型で入力する
  mapping: '{"Redmine上の担当者名": "SlackのメンバーID"}'
```

- `<redmine-api-key>`: RedmineのAPIアクセスキー
- `<slack-bot-token>`: Slack AppのBot Token
- `<slack-channel-id>`: チケットを通知するチャンネルのID
- Redmine上の担当者名とSlackのメンバーIDの対応


### 4. デプロイする
```
$ kubectl apply -f /path/to/redmine-ticket-notifier/deploy
deployment.apps/redmine-ticket-notifier created
secret/redmine-api-secret created
secret/slack-app-secret created
secret/user-mapping-secret created
$ 
```

## 通知されたメッセージのSlack上での表示例

チケットのURLと、チケット内に記載された概要・担当者名・発行日が記載されたメッセージが通知されます。


Redmineに作成されたチケットの例

<img width="458" height="441" alt="Image" src="https://github.com/user-attachments/assets/d0192949-0fd5-47a8-8484-2ae6cb54a311" />

上記のチケットが発行されたことを通知したメッセージの表示例

<img width="507" height="126" alt="Image" src="https://github.com/user-attachments/assets/c58cfec1-e4e5-4931-a1b8-9bd94ef3c8a7" />

チケットのステータスが「完了」に変更されると、メッセージに「✅」のリアクションが追加される。

<img width="174" height="149" alt="Image" src="https://github.com/user-attachments/assets/11d32b73-16ba-4580-8f21-d24f54193dab" />

チケットのトラッカーが特定のトラッカーに変更されると、メッセージに「🗑️」のリアクションが追加される。

<img width="180" height="137" alt="Image" src="https://github.com/user-attachments/assets/e8b7611a-cda8-4fd4-8581-4173697ba616" />
