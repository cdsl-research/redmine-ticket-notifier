import requests
import json
import time
import signal
import sys
import os
from datetime import datetime, timedelta, timezone

# --- 設定（環境変数で上書き可能） ---
REDMINE_URL = os.getenv("REDMINE_URL", "<redmine-url>")
REDMINE_API_KEY = os.getenv("REDMINE_API_KEY", "<redmine-api-key>")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "<slack-webhook-url>")
# 前回チェック時刻を保存するファイル
LAST_CHECK_FILE = os.getenv("LAST_CHECK_FILE", "last_check.txt")
# 通知済みチケットIDを保存するファイル
NOTIFIED_TICKETS_FILE = os.getenv("NOTIFIED_TICKETS_FILE", "notified_tickets.txt")
# ポーリング間隔（秒）
POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", "<polling-interval>"))
# 通知するトラッカーID（カンマ区切り、空なら全て通知）
_notify_ids_raw = os.getenv("NOTIFY_TRACKER_IDS", "<notify-tracker-id").strip()
if _notify_ids_raw == "":
    NOTIFY_TRACKER_IDS = []
else:
    try:
        NOTIFY_TRACKER_IDS = [int(x.strip()) for x in _notify_ids_raw.split(",") if x.strip()]
    except ValueError:
        # 不正な環境変数は無視して全トラッカー通知にフォールバック
        NOTIFY_TRACKER_IDS = []

# 再通知間隔（秒）（6時間＝21600秒）
RENOTIFY_INTERVAL_SECONDS = int(os.getenv("RENOTIFY_INTERVAL_SECONDS", "<renotify-interval-seconds>"))
# 再通知対象チケットを保存するファイル
RENOTIFY_TICKETS_FILE = os.getenv("RENOTIFY_TICKETS_FILE", "renotify_tickets.txt")
# 完了したチケットを保存するファイルファイル
COMPLETED_TICKETS_FILE = os.getenv("COMPLETED_TICKETS_FILE", "completed_tickets.txt")
# Redmine担当者とSlackユーザネームのマッピング（JSON形式）
USER_MAPPING_JSON = os.getenv("USER_MAPPING_JSON", '{"Redmine上の担当者名": "SlackのメンバーID"}')

# ユーザーマッピングを読み込む
try:
    USER_MAPPING = json.loads(USER_MAPPING_JSON)
except json.JSONDecodeError:
    print("ユーザーマッピングのJSON形式が不正です。")

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
    
    print(f"前回チェック時刻以降のチケットを検索: {last_check_time}")
    
    params = {
        "sort": "created_on:desc",
        "created_on": f">={formatted_time}",
        "limit": 100  # 最大100件取得
    }

    try:
        response = requests.get(api_url, headers=headers, params=params)
        print(f"API URL: {response.url}")
        print(f"Response Status: {response.status_code}")
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
                    print(f"新しいチケット#{issue['id']} 発見: {created_on}")
            except Exception as e:
                print(f"チケット#{issue['id']} の日時解析エラー: {e}")
                continue
        
        print(f"新しいチケット: {len(filtered_issues)}件")
        return filtered_issues
    except requests.exceptions.RequestException as e:
        print(f"Redmine API呼び出しエラー: {e}")
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

def load_renotify_tickets():
    """
    再通知対象チケットIDとその通知時刻の辞書を読み込む
    """
    try:
        with open(RENOTIFY_TICKETS_FILE, "r") as f:
            content = f.read().strip()
            if content:
                renotify_tickets = {}
                for line in content.split('\n'):
                    if line.strip():
                        parts = line.strip().split(',')
                        if len(parts) == 2:
                            ticket_id = int(parts[0])
                            notify_time = parts[1]
                            renotify_tickets[ticket_id] = notify_time
                return renotify_tickets
            return {}
    except FileNotFoundError:
        return {}

def save_renotify_ticket(ticket_id, notify_time):
    """
    再通知対象チケットIDとその通知時刻をファイルに追加する
    """
    renotify_tickets = load_renotify_tickets()
    renotify_tickets[ticket_id] = notify_time
    
    with open(RENOTIFY_TICKETS_FILE, "w") as f:
        for ticket_id, notify_time in renotify_tickets.items():
            f.write(f"{ticket_id},{notify_time}\n")

def remove_renotify_ticket(ticket_id):
    """
    再通知対象チケットIDをファイルから削除する
    """
    renotify_tickets = load_renotify_tickets()
    if ticket_id in renotify_tickets:
        del renotify_tickets[ticket_id]
        
        with open(RENOTIFY_TICKETS_FILE, "w") as f:
            for ticket_id, notify_time in renotify_tickets.items():
                f.write(f"{ticket_id},{notify_time}\n")

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
    
    # 再通知対象からも削除
    renotify_tickets = load_renotify_tickets()
    for ticket_id in deleted_ticket_ids:
        if ticket_id in renotify_tickets:
            del renotify_tickets[ticket_id]
    
    with open(RENOTIFY_TICKETS_FILE, "w") as f:
        for ticket_id, notify_time in renotify_tickets.items():
            f.write(f"{ticket_id},{notify_time}\n")
    
    print(f"削除されたチケット {len(deleted_ticket_ids)}件を追跡対象から除外しました")

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
        print(f"チケット#{ticket_id}のステータス取得エラー: {e}")
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
        print(f"チケット#{ticket_id}の情報取得エラー: {e}")
        return None

def check_completed_tickets():
    """
    通知済みチケットの完了をチェックし、完了した場合は通知する
    """
    notified_tickets = load_notified_tickets()
    completed_tickets = load_completed_tickets()
    newly_completed = []
    deleted_tickets = []
    
    for ticket_id in notified_tickets:
        if ticket_id not in completed_tickets:
            # チケットの詳細情報を取得
            issue = get_ticket_info(ticket_id)
            if issue is None:
                # チケットが削除された場合
                deleted_tickets.append(ticket_id)
                print(f"チケット#{ticket_id}は削除されました - 追跡対象から除外します")
                continue
            elif issue:
                status = issue.get("status", {}).get("name", "不明")
                # 完了ステータスかチェック（「完了」「終了」「クローズ」など）
                if status in ["完了", "終了", "クローズ", "Closed", "Resolved", "Done"]:
                    newly_completed.append(issue)
                    save_completed_ticket(ticket_id)
                    print(f"チケット#{ticket_id}が完了しました (ステータス: {status})")
    
    # 削除されたチケットを追跡対象から除外
    if deleted_tickets:
        remove_deleted_tickets_from_tracking(deleted_tickets)
    
    return newly_completed

def filter_quick_completion_tickets(new_issues, completed_issues):
    """
    10分以内に発行と完了が済んだチケットを除外する
    """
    # 新規チケットのIDセット
    new_ticket_ids = {issue['id'] for issue in new_issues}
    
    # 完了チケットのIDセット
    completed_ticket_ids = {issue['id'] for issue in completed_issues}
    
    # 10分以内に発行と完了が済んだチケットのID
    quick_completion_ids = new_ticket_ids.intersection(completed_ticket_ids)
    
    # 新規チケットから除外
    filtered_new_issues = [issue for issue in new_issues if issue['id'] not in quick_completion_ids]
    
    # 完了チケットから除外
    filtered_completed_issues = [issue for issue in completed_issues if issue['id'] not in quick_completion_ids]
    
    if quick_completion_ids:
        print(f"10分以内に発行と完了が済んだチケット {len(quick_completion_ids)}件を除外しました: {list(quick_completion_ids)}")
    
    return filtered_new_issues, filtered_completed_issues

def check_renotify_tickets():
    """
    再通知対象チケットのステータスをチェックし、未着手の場合は再通知する
    """
    renotify_tickets = load_renotify_tickets()
    current_time = datetime.now(timezone.utc)
    tickets_to_remove = []
    
    for ticket_id, notify_time_str in renotify_tickets.items():
        try:
            # 通知時刻を解析
            notify_time = datetime.fromisoformat(notify_time_str.replace('Z', '+00:00'))
            
            # 再通知間隔が経過しているかチェック
            if current_time >= notify_time + timedelta(seconds=RENOTIFY_INTERVAL_SECONDS):
                # チケットのステータスを取得
                status = get_ticket_status(ticket_id)
                
                if status == "削除済み":
                    # チケットが削除された場合は追跡対象から除外
                    print(f"チケット#{ticket_id}は削除されました - 再通知対象から除外")
                    tickets_to_remove.append(ticket_id)
                elif status == "未着手":
                    # 再通知を実行
                    print(f"再通知: チケット#{ticket_id} (ステータス: {status})")
                    
                    # 再通知用のメッセージを送信
                    send_renotify_slack_notification(ticket_id, status)
                    
                    # 再通知済みとして削除
                    tickets_to_remove.append(ticket_id)
                else:
                    # ステータスが変更されている場合は削除
                    print(f"チケット#{ticket_id}のステータスが変更されました: {status} - 再通知対象から除外")
                    tickets_to_remove.append(ticket_id)
                    
        except Exception as e:
            print(f"チケット#{ticket_id}の再通知チェックエラー: {e}")
            continue
    
    # 処理済みのチケットを削除
    for ticket_id in tickets_to_remove:
        remove_renotify_ticket(ticket_id)
    
    return len(tickets_to_remove)

def display_ticket_info(issue):
    """
    チケット情報をコンソールに表示する
    """
    print("=" * 60)
    print(f"新しいチケット")
    print("=" * 60)
    print(f"チケットID: #{issue['id']}")
    print(f"プロジェクト: {issue['project']['name']}")
    print(f"トラッカー: {issue.get('tracker', {}).get('name', '不明')}")
    print(f"件名: {issue['subject']}")
    print(f"作成者: {issue.get('author', {}).get('name', '不明')}")
    print(f"作成日時: {issue['created_on']}")
    print(f"ステータス: {issue.get('status', {}).get('name', '不明')}")
    print(f"優先度: {issue.get('priority', {}).get('name', '不明')}")
    print(f"URL: {REDMINE_URL}/issues/{issue['id']}")
    print("=" * 60)

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
        text += f"{mention_text}"
    
    # Slackに送信するメッセージを構築
    message = {
        "text": text,
        "attachments": [
            {
                "color": "#A31E24",
                "title": f"{issue['tracker']['name']} #{issue['id']}: {issue['subject']}",
                "title_link": f"{REDMINE_URL}/issues/{issue['id']}",
                "footer": f"{issue.get('description', '')}\n担当者: {issue.get('assigned_to', {}).get('name', '未割り当て')}",
                "ts": int(datetime.fromisoformat(issue['created_on'].replace('Z', '+00:00')).timestamp())
            }
        ]
    }
    
    try:
        response = requests.post(SLACK_WEBHOOK_URL, data=json.dumps(message), headers={'Content-Type': 'application/json'})
        response.raise_for_status()
        print(f"チケット#{issue['id']}のSlack通知を送信しました。")
    except requests.exceptions.RequestException as e:
        print(f"Slack通知送信エラー: {e}")

def send_renotify_slack_notification(ticket_id, status):
    """
    再通知用のSlackメッセージを送信する
    """
    # チケットの詳細情報を取得して担当者を特定
    issue = get_ticket_info(ticket_id)
    assigned_to_name = issue.get('assigned_to', {}).get('name', '未割り当て') if issue else '未割り当て'
    mention_text = create_mention_text(assigned_to_name)
    
    # 本文を作成
    text = f"下記のチケットが未着手です。"
    if mention_text:
        text += f"{mention_text}"
    
    # 再通知用のメッセージを構築
    message = {
        "text": text,
        "attachments": [
            {
                "color": "#FFCC01",
                "title": f"{issue['tracker']['name']} #{issue['id']}: {issue['subject']}",
                "title_link": f"{REDMINE_URL}/issues/{issue['id']}",
                "footer": f"{issue.get('description', '')}\n担当者: {issue.get('assigned_to', {}).get('name', '未割り当て')}",
                "ts": int(datetime.fromisoformat(issue['created_on'].replace('Z', '+00:00')).timestamp())
            }
        ]
    }
    
    try:
        response = requests.post(SLACK_WEBHOOK_URL, data=json.dumps(message), headers={'Content-Type': 'application/json'})
        response.raise_for_status()
        print(f"チケット#{ticket_id}の再通知Slackメッセージを送信しました。")
    except requests.exceptions.RequestException as e:
        print(f"再通知Slack送信エラー: {e}")

def send_completion_slack_notification(issue):
    """
    完了通知用のSlackメッセージを送信する
    """
    # 担当者名を取得（@メンションは不要）
    assigned_to_name = issue.get('assigned_to', {}).get('name', '不明')
    
    # 本文を作成（@メンションなし）
    text = f"下記のチケットのステータスが完了になりました。"
    
    # 完了通知用のメッセージを構築
    message = {
        "text": text,
        "attachments": [
            {
                "color": "#28A745",  # 完了通知用の色（緑）
                "title": f"{issue['tracker']['name']} #{issue['id']}: {issue['subject']}",
                "title_link": f"{REDMINE_URL}/issues/{issue['id']}",
                "footer": f"{issue.get('description', '')}\n担当者: {issue.get('assigned_to', {}).get('name', '未割り当て')}",
                "ts": int(datetime.fromisoformat(issue['created_on'].replace('Z', '+00:00')).timestamp())
            }
        ]
    }
    
    try:
        response = requests.post(SLACK_WEBHOOK_URL, data=json.dumps(message), headers={'Content-Type': 'application/json'})
        response.raise_for_status()
        print(f"チケット#{issue['id']}の完了通知Slackメッセージを送信しました。")
    except requests.exceptions.RequestException as e:
        print(f"完了通知Slack送信エラー: {e}")

def signal_handler(sig, frame):
    """
    Ctrl+Cで終了する際のシグナルハンドラー
    """
    print("\n監視を停止します...")
    sys.exit(0)

def main():
    """
    メイン処理 - ポーリング方式でチケットを監視
    """
    # シグナルハンドラーを設定
    signal.signal(signal.SIGINT, signal_handler)
    
    print("Redmineチケット監視を開始します...")
    print(f"ポーリング間隔: {POLLING_INTERVAL}秒")
    print(f"再通知間隔: {RENOTIFY_INTERVAL_SECONDS}秒")
    if NOTIFY_TRACKER_IDS:
        print(f"通知対象トラッカーID: {NOTIFY_TRACKER_IDS}")
    else:
        print("通知対象: 全てのトラッカー")
    print("停止するには Ctrl+C を押してください")
    print("-" * 60)
    
    try:
        with open(LAST_CHECK_FILE, "r") as f:
            last_check_time = f.read().strip()
        print(f"前回チェック時刻: {last_check_time}")
    except FileNotFoundError:
        # ファイルがない場合は現在時刻を基準にする
        last_check_time = datetime.now(timezone.utc).isoformat()
        print(f"初回実行: {last_check_time}")
    
    # 通知済みチケット数を表示
    notified_tickets = load_notified_tickets()
    print(f"通知済みチケット数: {len(notified_tickets)}件")
    
    check_count = 0
    
    while True:
        check_count += 1
        current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        print(f"\nチェック #{check_count} - {current_time}")
        
        new_issues = get_new_issues(last_check_time)
        
        if new_issues:
            # トラッカーフィルタリングを適用
            tracker_filtered_issues = [issue for issue in new_issues if is_tracker_notification_target(issue)]
            
            # 通知済みチェックを適用
            final_filtered_issues = [issue for issue in tracker_filtered_issues if not is_already_notified(issue)]
            
            print(f"{len(new_issues)}件の新しいチケットが見つかりました。")
            if NOTIFY_TRACKER_IDS:
                print(f"通知対象トラッカー: {NOTIFY_TRACKER_IDS}")
                print(f"トラッカーフィルタ後: {len(tracker_filtered_issues)}件")
            
            print(f"未通知チケット: {len(final_filtered_issues)}件")
            
            for issue in final_filtered_issues:
                display_ticket_info(issue)
                send_slack_notification(issue)
                # 通知済みとして記録
                save_notified_ticket(issue['id'])
                # 再通知対象として登録（現在時刻を記録）
                current_time = datetime.now(timezone.utc).isoformat()
                save_renotify_ticket(issue['id'], current_time)
                print(f"チケット#{issue['id']}を通知済みとして記録しました。")
                print(f"チケット#{issue['id']}を再通知対象として登録しました。")
                
            # フィルタリングで除外されたチケットがある場合は表示
            tracker_excluded = len(new_issues) - len(tracker_filtered_issues)
            notified_excluded = len(tracker_filtered_issues) - len(final_filtered_issues)
            
            if tracker_excluded > 0:
                print(f"トラッカーフィルタにより {tracker_excluded}件のチケットを除外しました。")
            if notified_excluded > 0:
                print(f"既に通知済みのため {notified_excluded}件のチケットを除外しました。")
        else:
            print("新しいチケットはありませんでした。")
        
        # 完了したチケットをチェック
        print("\n完了したチケットをチェック中...")
        completed_issues = check_completed_tickets()
        
        # 10分以内に発行と完了が済んだチケットを除外
        if new_issues and completed_issues:
            new_issues, completed_issues = filter_quick_completion_tickets(new_issues, completed_issues)
        
        # 完了したチケットを通知
        if completed_issues:
            print(f"{len(completed_issues)}件のチケットが完了しました。")
            for issue in completed_issues:
                send_completion_slack_notification(issue)
        else:
            print("完了したチケットはありませんでした。")
        
        # 再通知対象チケットをチェック
        print("\n再通知対象チケットをチェック中...")
        renotify_count = check_renotify_tickets()
        if renotify_count > 0:
            print(f"{renotify_count}件のチケットを再通知しました。")
        else:
            print("再通知対象のチケットはありませんでした。")
        
        # 現在の時刻をファイルに保存
        with open(LAST_CHECK_FILE, "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
        
        print(f"{POLLING_INTERVAL}秒後に次のチェックを実行します...")
        time.sleep(POLLING_INTERVAL)

if __name__ == "__main__":
    main()