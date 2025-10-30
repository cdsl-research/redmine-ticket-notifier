# Redmine Ticket Notifier

## 概要
Redmineに登録されたチケットをSlackのチャンネルに通知します。

## 前提
K3sによるKubernetesクラスタにデプロイすることを前提としています．
またSlack Appを使用します。

## 環境構成
- Ubuntu Server 24.04.2 LTS
- K3s v1.30.6+k3s1
- Prometheus 2.53.1
- Alertmanager 0.27.0
- Redmine 6.0.4.stable
- Slack 4.46.101
- Python 3.12.3
    - requests ?.??.?
    - slack-sdk ?.??.?

<!-- - Docker version 27.5.1 -->

## インストール・セットアップ

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
```
### 2. 必要な認証情報を設定する
deploy/secret.yamlの`<redmine-api-key>`にRedmineのAPIアクセスキー、`<slack-bot-token>`にSlack AppのBot Token、`<slack-channel-id>`にアラートを通知するチャンネルのIDをそれぞれ入力します。

### 3. 

### 4. Kubernetesクラスターにデプロイする
```
$ kubectl apply -f /path/to/redmine-ticket-notifier/deploy
```

## 使用例

### Slackで通知される例

