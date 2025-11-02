# Redmine Ticket Notifier

## 概要
Redmineに登録されたチケットをSlackのチャンネルに通知します。

## 前提
K3sによるKubernetesクラスタにデプロイすることを前提としています．

またSlack Appを使用します。

## 機能
- 特定のトラッカーに新しく作成されたチケットの通知
- 通知メッセージにチケットの担当者のメンションを追加（チケットの担当者名とSlackのメンバーIDの紐付けが必要）
- チケットのステータスが完了に変更されると，該当チケットの通知メッセージに「✅」のリアクションを追加
- 特定のトラッカーにチケットが移動された場合に，該当チケットの通知メッセージに「🗑️」のリアクションを追加

## 環境構成
- Ubuntu Server 24.04.2 LTS
- K3s v1.30.6+k3s1
- Prometheus 2.53.1
- Alertmanager 0.27.0
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
### 2. 必要な認証情報やIDを設定する
```
$ cp deploy/secret.yaml.example deploy/secret.yaml
```
deploy/secret.yamlを編集し，`<redmine-api-key>`にRedmineのAPIアクセスキー、`<slack-bot-token>`にSlack AppのBot Token、`<slack-channel-id>`にチケットを通知するチャンネルのIDをそれぞれ設定してください。

### 3. デプロイする
```
$ kubectl apply -f /path/to/redmine-ticket-notifier/deploy
deployment.apps/redmine-ticket-notifier created
secret/redmine-api-secret created
secret/slack-app-secret created
secret/user-mapping-secret created
$ 
```

## 通知されたメッセージの表示例

<img width="387" height="128" alt="Image" src="https://github.com/user-attachments/assets/c5a75f08-9039-4974-a645-39cd47100706" />
