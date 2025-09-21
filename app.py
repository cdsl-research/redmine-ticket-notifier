import requests
import json
import time
import signal
import sys
import os
from datetime import datetime, timedelta, timezone
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# --- 設定（環境変数で上書き可能） ---
REDMINE_URL = os.getenv("REDMINE_URL", "<redmine-url>")
REDMINE_API_KEY = os.getenv("REDMINE_API_KEY", "<redmine-api-key>")
# Slack App設定
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "<slack-bot-token>")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "<slack-channel-id>")
# 前回チェック時刻を保存するファイル
LAST_CHECK_FILE = os.getenv("LAST_CHECK_FILE", "last_check.txt")
# 通知済みチケットIDを保存するファイル
NOTIFIED_TICKETS_FILE = os.getenv("NOTIFIED_TICKETS_FILE", "notified_tickets.txt")
# ポーリング間隔（秒）
POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", "<polling-interval>"))
# 通知するトラッカーID（カンマ区切り、空なら全て通知）
_notify_ids_raw = os.getenv("NOTIFY_TRACKER_IDS", "<notify-tracker-id>").strip()
if _notify_ids_raw == "":
    NOTIFY_TRACKER_IDS = []
else:
    try:
        NOTIFY_TRACKER_IDS = [int(x.strip()) for x in _notify_ids_raw.split(",") if x.strip()]
    except ValueError:
        # 不正な環境変数は無視して全トラッカー通知にフォールバック
        NOTIFY_TRACKER_IDS = []

# 未着手通知間隔（秒）（デフォルト：1時間＝3600秒）
PENDING_NOTIFICATION_INTERVAL_SECONDS = int(os.getenv("PENDING_NOTIFICATION_INTERVAL_SECONDS", "<pending-notification-interval-seconds>"))
# 完了したチケットを保存するファイルファイル
COMPLETED_TICKETS_FILE = os.getenv("COMPLETED_TICKETS_FILE", "completed_tickets.txt")
# チケットIDとSlackメッセージIDのマッピングを保存するファイル
MESSAGE_MAPPING_FILE = os.getenv("MESSAGE_MAPPING_FILE", "message_mapping.txt")
# チケットIDとトラッカーIDのマッピングを保存するファイル
TRACKER_MAPPING_FILE = os.getenv("TRACKER_MAPPING_FILE", "tracker_mapping.txt")
# チケットIDと作成時刻のマッピングを保存するファイル
CREATION_TIME_MAPPING_FILE = os.getenv("CREATION_TIME_MAPPING_FILE", "creation_time_mapping.txt")
# チケットIDと再通知メッセージIDのマッピングを保存するファイル
PENDING_MESSAGE_MAPPING_FILE = os.getenv("PENDING_MESSAGE_MAPPING_FILE", "pending_message_mapping.txt")
# Redmine担当者とSlackユーザネームのマッピング（JSON形式）
USER_MAPPING_JSON = os.getenv("USER_MAPPING_JSON", '{"Redmine上の担当者名": "SlackのメンバーID"}')
# 完了時のSlackリアクション絵文字
SLACK_COMPLETION_EMOJI = os.getenv("SLACK_COMPLETION_EMOJI", "white_check_mark")
# 削除時のSlackリアクション絵文字
SLACK_DELETION_EMOJI = os.getenv("SLACK_DELETION_EMOJI", "wastebasket")

# ユーザーマッピングを読み込む
try:
    USER_MAPPING = json.loads(USER_MAPPING_JSON)
except json.JSONDecodeError:
    print("ユーザーマッピングのJSON形式が不正です。")

# Slack Web APIクライアントを初期化
slack_client = WebClient(token=SLACK_BOT_TOKEN)

# --- 関数 ---
def get_new_issues(last_check_time):
    """
    指定した時刻以降に作成されたRedmineのチケットを取得する
    """
    api_url = f"{REDMINE_URL}/issues.json"
    headers = {
        "X-Redmine-API-Key": REDMINE_API_KEY
    }
    
    # Redmine API用の日時フォーマットに変換 (YYYY-MM-DD)
    try:
        dt = datetime.fromisoformat(last_check_time.replace('Z', '+00:00'))
        formatted_time = dt.strftime('%Y-%m-%d')
    except:
        # フォーマット変換に失敗した場合は現在時刻から1時間前を使用
        dt = datetime.now(timezone.utc) - timedelta(hours=1)
        formatted_time = dt.strftime('%Y-%m-%d')
    
    params = {
        "sort": "created_on:desc",
        "created_on": f">={formatted_time}",
        "limit": 100  # 最大100件取得
    }

    try:
        response = requests.get(api_url, headers=headers, params=params)
        if response.status_code != 200:
            print(f"Response Text: {response.text}")
        response.raise_for_status() # HTTPエラーが発生した場合に例外を発生させる
        data = response.json()
        all_issues = data.get("issues", [])
        
        # 前回チェック時刻以降のチケットをフィルタリング
        filtered_issues = []
        last_check_dt = datetime.fromisoformat(last_check_time.replace('Z', '+00:00'))
        
        for issue in all_issues:
            try:
                # チケットの作成日時を解析
                created_on = datetime.fromisoformat(issue['created_on'].replace('Z', '+00:00'))
                
                # 前回チェック時刻以降かチェック
                if created_on > last_check_dt:
                    filtered_issues.append(issue)
                    print(f"New ticket #{issue['id']} found: {created_on}")
            except Exception as e:
                print(f"Date parsing error for ticket #{issue['id']}: {e}")
                continue
        
        print(f"New tickets: {len(filtered_issues)}")
        return filtered_issues
    except requests.exceptions.RequestException as e:
        print(f"Redmine API call error: {e}")
        return []

def load_notified_tickets():
    """
    通知済みチケットIDのリストを読み込む
    """
    try:
        with open(NOTIFIED_TICKETS_FILE, "r") as f:
            content = f.read().strip()
            if content:
                return set(int(line.strip()) for line in content.split('\n') if line.strip())
            return set()
    except FileNotFoundError:
        return set()

def save_notified_ticket(ticket_id):
    """
    通知済みチケットIDをファイルに追加する
    """
    notified_tickets = load_notified_tickets()
    notified_tickets.add(ticket_id)
    
    with open(NOTIFIED_TICKETS_FILE, "w") as f:
        for ticket_id in sorted(notified_tickets):
            f.write(f"{ticket_id}\n")


def load_completed_tickets():
    """
    完了したチケットIDのセットを読み込む
    """
    try:
        with open(COMPLETED_TICKETS_FILE, "r") as f:
            content = f.read().strip()
            if content:
                return set(int(line.strip()) for line in content.split('\n') if line.strip())
            return set()
    except FileNotFoundError:
        return set()

def save_completed_ticket(ticket_id):
    """
    完了したチケットIDをファイルに追加する
    """
    completed_tickets = load_completed_tickets()
    completed_tickets.add(ticket_id)
    
    with open(COMPLETED_TICKETS_FILE, "w") as f:
        for ticket_id in sorted(completed_tickets):
            f.write(f"{ticket_id}\n")

def load_message_mapping():
    """
    チケットIDとSlackメッセージIDのマッピングを読み込む
    """
    try:
        with open(MESSAGE_MAPPING_FILE, "r") as f:
            content = f.read().strip()
            if content:
                message_mapping = {}
                for line in content.split('\n'):
                    if line.strip():
                        parts = line.strip().split(',')
                        if len(parts) == 2:
                            ticket_id = int(parts[0])
                            message_id = parts[1]
                            message_mapping[ticket_id] = message_id
                return message_mapping
            return {}
    except FileNotFoundError:
        return {}

def save_message_mapping(ticket_id, message_id):
    """
    チケットIDとSlackメッセージIDのマッピングを保存する
    """
    message_mapping = load_message_mapping()
    message_mapping[ticket_id] = message_id
    
    with open(MESSAGE_MAPPING_FILE, "w") as f:
        for ticket_id, message_id in message_mapping.items():
            f.write(f"{ticket_id},{message_id}\n")

def remove_message_mapping(ticket_id):
    """
    チケットIDのメッセージマッピングを削除する
    """
    message_mapping = load_message_mapping()
    if ticket_id in message_mapping:
        del message_mapping[ticket_id]
        
        with open(MESSAGE_MAPPING_FILE, "w") as f:
            for ticket_id, message_id in message_mapping.items():
                f.write(f"{ticket_id},{message_id}\n")


def load_tracker_mapping():
    """
    チケットIDとトラッカーIDのマッピングを読み込む
    """
    try:
        with open(TRACKER_MAPPING_FILE, "r") as f:
            content = f.read().strip()
            if content:
                tracker_mapping = {}
                for line in content.split('\n'):
                    if line.strip():
                        parts = line.strip().split(',')
                        if len(parts) == 2:
                            ticket_id = int(parts[0])
                            tracker_id = int(parts[1])
                            tracker_mapping[ticket_id] = tracker_id
                return tracker_mapping
            return {}
    except FileNotFoundError:
        return {}

def save_tracker_mapping(ticket_id, tracker_id):
    """
    チケットIDとトラッカーIDのマッピングを保存する
    """
    tracker_mapping = load_tracker_mapping()
    tracker_mapping[ticket_id] = tracker_id
    
    with open(TRACKER_MAPPING_FILE, "w") as f:
        for ticket_id, tracker_id in tracker_mapping.items():
            f.write(f"{ticket_id},{tracker_id}\n")

def remove_tracker_mapping(ticket_id):
    """
    チケットIDのトラッカーマッピングを削除する
    """
    tracker_mapping = load_tracker_mapping()
    if ticket_id in tracker_mapping:
        del tracker_mapping[ticket_id]
        
        with open(TRACKER_MAPPING_FILE, "w") as f:
            for ticket_id, tracker_id in tracker_mapping.items():
                f.write(f"{ticket_id},{tracker_id}\n")

def load_creation_time_mapping():
    """
    チケットIDと作成時刻のマッピングを読み込む
    """
    try:
        with open(CREATION_TIME_MAPPING_FILE, "r") as f:
            content = f.read().strip()
            if content:
                creation_time_mapping = {}
                for line in content.split('\n'):
                    if line.strip():
                        parts = line.strip().split(',')
                        if len(parts) == 2:
                            ticket_id = int(parts[0])
                            creation_time = parts[1]
                            creation_time_mapping[ticket_id] = creation_time
                return creation_time_mapping
            return {}
    except FileNotFoundError:
        return {}

def save_creation_time_mapping(ticket_id, creation_time):
    """
    チケットIDと作成時刻のマッピングを保存する
    """
    creation_time_mapping = load_creation_time_mapping()
    creation_time_mapping[ticket_id] = creation_time
    
    with open(CREATION_TIME_MAPPING_FILE, "w") as f:
        for ticket_id, creation_time in creation_time_mapping.items():
            f.write(f"{ticket_id},{creation_time}\n")

def remove_creation_time_mapping(ticket_id):
    """
    チケットIDの作成時刻マッピングを削除する
    """
    creation_time_mapping = load_creation_time_mapping()
    if ticket_id in creation_time_mapping:
        del creation_time_mapping[ticket_id]
        
        with open(CREATION_TIME_MAPPING_FILE, "w") as f:
            for ticket_id, creation_time in creation_time_mapping.items():
                f.write(f"{ticket_id},{creation_time}\n")

def load_pending_message_mapping():
    """
    チケットIDと再通知メッセージIDのマッピングを読み込む
    """
    try:
        with open(PENDING_MESSAGE_MAPPING_FILE, "r") as f:
            content = f.read().strip()
            if content:
                pending_message_mapping = {}
                for line in content.split('\n'):
                    if line.strip():
                        parts = line.strip().split(',')
                        if len(parts) == 2:
                            ticket_id = int(parts[0])
                            message_id = parts[1]
                            pending_message_mapping[ticket_id] = message_id
                return pending_message_mapping
            return {}
    except FileNotFoundError:
        return {}

def save_pending_message_mapping(ticket_id, message_id):
    """
    チケットIDと再通知メッセージIDのマッピングを保存する
    """
    pending_message_mapping = load_pending_message_mapping()
    pending_message_mapping[ticket_id] = message_id
    
    with open(PENDING_MESSAGE_MAPPING_FILE, "w") as f:
        for ticket_id, message_id in pending_message_mapping.items():
            f.write(f"{ticket_id},{message_id}\n")

def remove_pending_message_mapping(ticket_id):
    """
    チケットIDの再通知メッセージマッピングを削除する
    """
    pending_message_mapping = load_pending_message_mapping()
    if ticket_id in pending_message_mapping:
        del pending_message_mapping[ticket_id]
        
        with open(PENDING_MESSAGE_MAPPING_FILE, "w") as f:
            for ticket_id, message_id in pending_message_mapping.items():
                f.write(f"{ticket_id},{message_id}\n")

def remove_deleted_tickets_from_tracking(deleted_ticket_ids):
    """
    削除されたチケットを追跡対象から除外する
    """
    # 通知済みチケットから削除
    notified_tickets = load_notified_tickets()
    for ticket_id in deleted_ticket_ids:
        if ticket_id in notified_tickets:
            notified_tickets.remove(ticket_id)
    
    with open(NOTIFIED_TICKETS_FILE, "w") as f:
        for ticket_id in sorted(notified_tickets):
            f.write(f"{ticket_id}\n")
    
    # メッセージマッピングからも削除
    for ticket_id in deleted_ticket_ids:
        remove_message_mapping(ticket_id)
    
    
    # トラッカーマッピングからも削除
    for ticket_id in deleted_ticket_ids:
        remove_tracker_mapping(ticket_id)
    
    # 作成時刻マッピングからも削除
    for ticket_id in deleted_ticket_ids:
        remove_creation_time_mapping(ticket_id)
    
    # 再通知メッセージマッピングからも削除
    for ticket_id in deleted_ticket_ids:
        remove_pending_message_mapping(ticket_id)
    
    if deleted_ticket_ids:
        print(f"  CLEANUP: Removed {len(deleted_ticket_ids)} deleted tickets from tracking")

def get_slack_username(redmine_user_name):
    """
    Redmineの担当者名からSlackのユーザネームを取得する
    """
    if not redmine_user_name:
        return None
    return USER_MAPPING.get(redmine_user_name, None)

def create_mention_text(redmine_user_name):
    """
    Redmineの担当者名からSlackの@メンション文字列を作成する
    """
    slack_username = get_slack_username(redmine_user_name)
    if slack_username:
        return f"<@{slack_username}>"
    return None

def is_tracker_notification_target(issue):
    """
    指定されたトラッカーのチケットかどうかを判定する
    """
    if not NOTIFY_TRACKER_IDS:  # 空の場合は全て通知
        return True
    
    tracker_id = issue.get('tracker', {}).get('id')
    if tracker_id in NOTIFY_TRACKER_IDS:
        return True
    
    return False

def is_already_notified(issue):
    """
    既に通知済みのチケットかどうかを判定する
    """
    notified_tickets = load_notified_tickets()
    return issue['id'] in notified_tickets

def get_ticket_status(ticket_id):
    """
    指定されたチケットIDのステータスを取得する
    """
    api_url = f"{REDMINE_URL}/issues/{ticket_id}.json"
    headers = {
        "X-Redmine-API-Key": REDMINE_API_KEY
    }
    
    try:
        response = requests.get(api_url, headers=headers)
        if response.status_code == 404:
            return "削除済み"
        response.raise_for_status()
        data = response.json()
        issue = data.get("issue", {})
        return issue.get("status", {}).get("name", "不明")
    except requests.exceptions.RequestException as e:
        print(f"Error getting status for ticket #{ticket_id}: {e}")
        return "不明"

def get_ticket_info(ticket_id):
    """
    指定されたチケットIDの詳細情報を取得する
    """
    api_url = f"{REDMINE_URL}/issues/{ticket_id}.json"
    headers = {
        "X-Redmine-API-Key": REDMINE_API_KEY
    }
    
    try:
        response = requests.get(api_url, headers=headers)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        return data.get("issue", {})
    except requests.exceptions.RequestException as e:
        print(f"Error getting info for ticket #{ticket_id}: {e}")
        return None

def check_completed_tickets():
    """
    通知済みチケットの完了をチェックし、完了した場合はリアクションを追加する
    また、特定の条件でメッセージを削除する
    """
    notified_tickets = load_notified_tickets()
    completed_tickets = load_completed_tickets()
    tracker_mapping = load_tracker_mapping()
    newly_completed = []
    deleted_tickets = []
    
    for ticket_id in notified_tickets:
        if ticket_id not in completed_tickets:
            # チケットの詳細情報を取得
            issue = get_ticket_info(ticket_id)
            if issue is None:
                # チケットが削除された場合 - 両方のメッセージにゴミ箱リアクションを追加
                deleted_tickets.append(ticket_id)
                original_reaction = add_deletion_reaction(ticket_id)
                pending_reaction = add_pending_deletion_reaction(ticket_id)
                
                if original_reaction and pending_reaction:
                    print(f"  TICKET DELETED: #{ticket_id} (Both messages marked with wastebasket reaction)")
                elif original_reaction:
                    print(f"  TICKET DELETED: #{ticket_id} (Original message marked with wastebasket reaction)")
                elif pending_reaction:
                    print(f"  TICKET DELETED: #{ticket_id} (Pending message marked with wastebasket reaction)")
                else:
                    print(f"  TICKET DELETED: #{ticket_id} (No messages found)")
                continue
            elif issue:
                status = issue.get("status", {}).get("name", "不明")
                current_tracker_id = issue.get("tracker", {}).get("id")
                original_tracker_id = tracker_mapping.get(ticket_id)
                
                # 完了ステータスかチェック（「完了」「終了」「クローズ」など）
                if status in ["完了", "終了", "クローズ", "Closed", "Resolved", "Done"]:
                    newly_completed.append(issue)
                    save_completed_ticket(ticket_id)
                    
                    # トラッカーが変更されている場合は両方のメッセージにゴミ箱リアクションを追加
                    if original_tracker_id and current_tracker_id != original_tracker_id:
                        original_reaction = add_deletion_reaction(ticket_id)
                        pending_reaction = add_pending_deletion_reaction(ticket_id)
                        
                        if original_reaction and pending_reaction:
                            print(f"  COMPLETED (tracker changed): #{ticket_id} - {issue.get('subject', 'No subject')} (Both messages marked with wastebasket reaction)")
                        elif original_reaction:
                            print(f"  COMPLETED (tracker changed): #{ticket_id} - {issue.get('subject', 'No subject')} (Original message marked with wastebasket reaction)")
                        elif pending_reaction:
                            print(f"  COMPLETED (tracker changed): #{ticket_id} - {issue.get('subject', 'No subject')} (Pending message marked with wastebasket reaction)")
                        else:
                            print(f"  COMPLETED (tracker changed): #{ticket_id} - {issue.get('subject', 'No subject')} (No messages found)")
                    else:
                        # 元のメッセージにリアクションを追加
                        add_completion_reaction(ticket_id)
                        # 再通知メッセージにもリアクションを追加
                        add_pending_completion_reaction(ticket_id)
                        print(f"  COMPLETED: #{ticket_id} - {issue.get('subject', 'No subject')}")
                    
                    # トラッカーマッピングを削除
                    remove_tracker_mapping(ticket_id)
                    # 作成時刻マッピングを削除
                    remove_creation_time_mapping(ticket_id)
                    # 再通知メッセージマッピングを削除
                    remove_pending_message_mapping(ticket_id)
    
    # 削除されたチケットを追跡対象から除外
    if deleted_tickets:
        remove_deleted_tickets_from_tracking(deleted_tickets)
    
    return newly_completed



def display_ticket_info(issue):
    """
    チケット情報をコンソールに表示する
    """
    project = issue.get('project', {}).get('name', 'Unknown')
    tracker = issue.get('tracker', {}).get('name', 'Unknown')
    subject = issue.get('subject', 'No subject')
    author = issue.get('author', {}).get('name', 'Unknown')
    created_on = issue.get('created_on', 'Unknown')
    status = issue.get('status', {}).get('name', 'Unknown')
    priority = issue.get('priority', {}).get('name', 'Unknown')
    url = f"{REDMINE_URL}/issues/{issue['id']}"
    
    print(f"  NEW: #{issue['id']} - {subject} | Project: {project} | Tracker: {tracker} | Author: {author} | Status: {status} | Priority: {priority} | Created: {created_on} | URL: {url}")

def send_slack_notification(issue):
    """
    Slackにチケット情報を送信する
    """
    # 担当者の@メンションを作成
    assigned_to_name = issue.get('assigned_to', {}).get('name', '未割り当て')
    mention_text = create_mention_text(assigned_to_name)
    
    # 本文を作成
    text = f"新しいチケットが作成されました。"
    if mention_text:
        text += f" {mention_text}"
    
    # Slackに送信するメッセージを構築
    message = {
        "text": text,
        "attachments": [
            {
                "color": "#ae1500",
                "title": f"{issue['tracker']['name']} #{issue['id']}: {issue['subject']}",
                "title_link": f"{REDMINE_URL}/issues/{issue['id']}",
                "footer": f"{issue.get('description', '')}\n担当者: {issue.get('assigned_to', {}).get('name', '未割り当て')}",
                "ts": int(datetime.fromisoformat(issue['created_on'].replace('Z', '+00:00')).timestamp())
            }
        ]
    }
    
    try:
        response = slack_client.chat_postMessage(
            channel=SLACK_CHANNEL_ID,
            text=text,
            attachments=message["attachments"]
        )
        # メッセージIDを保存
        message_id = response['ts']
        save_message_mapping(issue['id'], message_id)
        pass  # 通知送信は成功
    except SlackApiError as e:
        print(f"  ERROR: Failed to send notification for #{issue['id']}: {e.response['error']}")

def send_pending_notification_with_mention(issue):
    """
    未着手チケットの通知をSlackに送信する（@メンション付き）
    """
    # 担当者の@メンションを作成
    assigned_to_name = issue.get('assigned_to', {}).get('name', '未割り当て')
    mention_text = create_mention_text(assigned_to_name)
    
    # 本文を作成
    text = f"未着手のチケットがあります。"
    if mention_text:
        text += f" {mention_text}"
    
    # Slackに送信するメッセージを構築
    message = {
        "text": text,
        "attachments": [
            {
                "color": "#FFCC01",  # 黄色
                "title": f"{issue['tracker']['name']} #{issue['id']}: {issue['subject']}",
                "title_link": f"{REDMINE_URL}/issues/{issue['id']}",
                "footer": f"{issue.get('description', '')}\n担当者: {issue.get('assigned_to', {}).get('name', '未割り当て')}",
                "ts": int(datetime.fromisoformat(issue['created_on'].replace('Z', '+00:00')).timestamp())
            }
        ]
    }
    
    try:
        response = slack_client.chat_postMessage(
            channel=SLACK_CHANNEL_ID,
            text=text,
            attachments=message["attachments"]
        )
        # 再通知メッセージIDを保存
        message_id = response['ts']
        save_pending_message_mapping(issue['id'], message_id)
        return True
    except SlackApiError as e:
        print(f"  ERROR: Failed to send pending notification for #{issue['id']}: {e.response['error']}")
        return False


def add_completion_reaction(ticket_id):
    """
    完了したチケットの元のメッセージにリアクションを追加する
    """
    message_mapping = load_message_mapping()
    message_id = message_mapping.get(ticket_id)
    
    if not message_id:
        return
    
    try:
        response = slack_client.reactions_add(
            channel=SLACK_CHANNEL_ID,
            timestamp=message_id,
            name=SLACK_COMPLETION_EMOJI  # 完了時のリアクション絵文字
        )
        pass  # リアクション追加は成功
    except SlackApiError as e:
        print(f"  ERROR: Failed to add reaction for #{ticket_id}: {e.response['error']}")

def add_pending_completion_reaction(ticket_id):
    """
    完了したチケットの再通知メッセージに完了リアクションを追加する
    """
    pending_message_mapping = load_pending_message_mapping()
    message_id = pending_message_mapping.get(ticket_id)
    
    if not message_id:
        return False
    
    try:
        response = slack_client.reactions_add(
            channel=SLACK_CHANNEL_ID,
            timestamp=message_id,
            name=SLACK_COMPLETION_EMOJI  # 完了時のリアクション絵文字
        )
        return True
    except SlackApiError as e:
        print(f"  ERROR: Failed to add pending completion reaction for #{ticket_id}: {e.response['error']}")
        return False

def add_deletion_reaction(ticket_id):
    """
    削除されたチケットの元のメッセージにゴミ箱リアクションを追加する
    """
    message_mapping = load_message_mapping()
    message_id = message_mapping.get(ticket_id)
    
    if not message_id:
        return False
    
    try:
        response = slack_client.reactions_add(
            channel=SLACK_CHANNEL_ID,
            timestamp=message_id,
            name=SLACK_DELETION_EMOJI  # 削除時のリアクション絵文字
        )
        return True
    except SlackApiError as e:
        print(f"  ERROR: Failed to add deletion reaction for #{ticket_id}: {e.response['error']}")
        return False

def add_pending_deletion_reaction(ticket_id):
    """
    削除されたチケットの再通知メッセージにゴミ箱リアクションを追加する
    """
    pending_message_mapping = load_pending_message_mapping()
    message_id = pending_message_mapping.get(ticket_id)
    
    if not message_id:
        return False
    
    try:
        response = slack_client.reactions_add(
            channel=SLACK_CHANNEL_ID,
            timestamp=message_id,
            name=SLACK_DELETION_EMOJI  # 削除時のリアクション絵文字
        )
        return True
    except SlackApiError as e:
        print(f"  ERROR: Failed to add pending deletion reaction for #{ticket_id}: {e.response['error']}")
        return False

def delete_slack_message(ticket_id):
    """
    チケットのSlackメッセージを削除する
    """
    message_mapping = load_message_mapping()
    message_id = message_mapping.get(ticket_id)
    
    if not message_id:
        return False
    
    try:
        response = slack_client.chat_delete(
            channel=SLACK_CHANNEL_ID,
            ts=message_id
        )
        # メッセージマッピングからも削除
        remove_message_mapping(ticket_id)
        return True
    except SlackApiError as e:
        print(f"  ERROR: Failed to delete message for #{ticket_id}: {e.response['error']}")
        return False

def check_pending_tickets():
    """
    通知済みチケットの未着手状況をチェックし、指定時間経過後に未着手の場合は通知する
    """
    notified_tickets = load_notified_tickets()
    completed_tickets = load_completed_tickets()
    creation_time_mapping = load_creation_time_mapping()
    current_time = datetime.now(timezone.utc)
    
    for ticket_id in notified_tickets:
        if ticket_id not in completed_tickets:
            # チケットの詳細情報を取得
            issue = get_ticket_info(ticket_id)
            if issue is None:
                # チケットが削除された場合
                continue
            elif issue:
                status = issue.get("status", {}).get("name", "不明")
                # 未着手ステータスかチェック（「未着手」のみ）
                if status in ["未着手"]:
                    # 作成時刻を取得
                    creation_time_str = creation_time_mapping.get(ticket_id)
                    if creation_time_str:
                        try:
                            creation_time = datetime.fromisoformat(creation_time_str.replace('Z', '+00:00'))
                            # 指定時間経過しているかチェック
                            time_elapsed = (current_time - creation_time).total_seconds()
                            if time_elapsed >= PENDING_NOTIFICATION_INTERVAL_SECONDS:
                                # 未着手通知を送信
                                if send_pending_notification_with_mention(issue):
                                    print(f"  PENDING NOTIFICATION: #{ticket_id} - {issue.get('subject', 'No subject')} (elapsed: {int(time_elapsed/60)} minutes)")
                                    # 作成時刻マッピングから削除（一度通知したら再通知しない）
                                    remove_creation_time_mapping(ticket_id)
                        except Exception as e:
                            print(f"  ERROR: Failed to parse creation time for #{ticket_id}: {e}")
                            continue


def signal_handler(sig, frame):
    """
    Ctrl+Cで終了する際のシグナルハンドラー
    """
    print("\nStopping monitoring...")
    sys.exit(0)

def main():
    """
    メイン処理 - ポーリング方式でチケットを監視
    """
    # シグナルハンドラーを設定
    signal.signal(signal.SIGINT, signal_handler)
    
    print("Starting Redmine ticket monitoring...")
    print(f"Polling interval: {POLLING_INTERVAL}s, Pending notification interval: {PENDING_NOTIFICATION_INTERVAL_SECONDS}s")
    if NOTIFY_TRACKER_IDS:
        print(f"Target trackers: {NOTIFY_TRACKER_IDS}")
    else:
        print("Target: All trackers")
    print("Press Ctrl+C to stop")
    print("-" * 60)
    
    try:
        with open(LAST_CHECK_FILE, "r") as f:
            last_check_time = f.read().strip()
        print(f"Last check time: {last_check_time}")
    except FileNotFoundError:
        # ファイルがない場合は現在時刻を基準にする
        last_check_time = datetime.now(timezone.utc).isoformat()
        print(f"First run: {last_check_time}")
    
    check_count = 0
    
    while True:
        check_count += 1
        current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        print(f"\n[{current_time}] Check #{check_count}")
        
        new_issues = get_new_issues(last_check_time)
        
        if new_issues:
            # トラッカーフィルタリングを適用
            tracker_filtered_issues = [issue for issue in new_issues if is_tracker_notification_target(issue)]
            
            # 通知済みチェックを適用
            final_filtered_issues = [issue for issue in tracker_filtered_issues if not is_already_notified(issue)]
            
            if final_filtered_issues:
                print(f"Found {len(final_filtered_issues)} new tickets to notify")
                
                for issue in final_filtered_issues:
                    print(f"  NEW: #{issue['id']} - {issue['subject']} ({issue.get('tracker', {}).get('name', 'Unknown')})")
                    send_slack_notification(issue)
                    # 通知済みとして記録
                    save_notified_ticket(issue['id'])
                    # トラッカーIDを保存
                    tracker_id = issue.get('tracker', {}).get('id')
                    if tracker_id:
                        save_tracker_mapping(issue['id'], tracker_id)
                    # 作成時刻を保存（未着手通知用）
                    creation_time = issue.get('created_on', '')
                    if creation_time:
                        save_creation_time_mapping(issue['id'], creation_time)
            else:
                print("No new tickets to notify")
        else:
            print("No new tickets found")
        
        # 完了したチケットをチェック
        completed_issues = check_completed_tickets()
        
        # 未着手チケットのチェック
        check_pending_tickets()
        
        
        # 現在の時刻をファイルに保存
        with open(LAST_CHECK_FILE, "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
        
        time.sleep(POLLING_INTERVAL)

if __name__ == "__main__":
    main()