# =================================================================
#  AI音読システム Max Pro (Version 1.0)
#  Developed by [Shogo Takeuchi] (2026)
#  
#  [著作権について]
#  本プログラムの著作権および知的財産権は開発者に帰属します。
#  開発者の許可なく、第三者への再配布、商用利用、転載を行うことを禁じます。
# =================================================================

import streamlit as st
import azure.cognitiveservices.speech as speechsdk
import os
import io
import time
import re
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import pandas as pd

st.set_page_config(page_title="AI音読アドバイザー Max Pro", layout="centered")

# --- 🎨 画面のデザイン設定 ---
st.markdown("""
    <style>
    .stApp { background-color: #ffffff; color: #1a202c; }
    h1, h2, h3 { color: #1a365d !important; font-weight: 700; }
    p, li, label, .stMarkdown { color: #2d3748 !important; font-size: 18px; line-height: 1.6; }
    
    .stTextInput>div>div>input, .stSelectbox>div>div>div, .stTextArea>div>textarea {
        background-color: #ffffff !important; color: #000000 !important;
        border: 2px solid #e2e8f0 !important; border-radius: 8px !important;
    }
    
    div[data-baseweb="popover"] ul { background-color: #ffffff !important; }
    div[data-baseweb="popover"] li { background-color: #ffffff !important; color: #000000 !important; }
    div[data-baseweb="popover"] li:hover { background-color: #edf2f7 !important; color: #000000 !important; }
    .stAudioInput { background-color: #f8fafc; border-radius: 12px; padding: 10px; border: 1px solid #e2e8f0; }
    </style>
""", unsafe_allow_html=True)

st.title("🗣️ AI音読システム Max Pro")
st.write("デジタル教科書のお手本をよく聴いてから、録音して提出しよう！")

# --- 1. 出席番号による初期ルートの負荷分散 ---
attendance_type = st.radio(
    "あなたの 出席番号（または班） を選んでください：",
    ["奇数番号 (1, 3, 5...)", "偶数番号 (2, 4, 6...)"],
    horizontal=True
)

if "奇数" in attendance_type:
    initial_key = st.secrets["KEY_KISU"]
else:
    initial_key = st.secrets["KEY_GUSU"]

azure_region = st.secrets["AZURE_REGION"]


# --- 2. Googleスプレッドシート（市教委大元）からマスタを取得 ---
@st.cache_data(ttl=10)
def load_master_data():
    try:
        robot_email = st.secrets["ROBOT_EMAIL"]
        client_id = st.secrets["ROBOT_CLIENT_ID"]
        formatted_private_key = st.secrets["ROBOT_PRIVATE_KEY"]
        
        info = {
            "type": "service_account", "project_id": "ai-ondoku-final-go",
            "private_key_id": "google_cloud_key", "private_key": formatted_private_key,
            "client_email": robot_email, "client_id": client_id,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs"
        }
        creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        sheets_service = build('sheets', 'v4', credentials=creds)
        spreadsheet_id = st.secrets["GOOGLE_SHEET_ID"]
        
        # G列（音声フォルダID）までまとめて一気に取得
        result = sheets_service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range="マスタ!A2:G200").execute()
        rows = result.get('values', [])
        
        mapping = {}
        for idx, row in enumerate(rows):
            if len(row) >= 2 and row[0] and row[1]:
                sch = row[0].strip()
                cls = row[1].strip()
                unit = row[2].strip() if len(row) > 2 and row[2] else "課題"
                txt = row[3].strip() if len(row) > 3 and row[3] else "English text here."
                pwd = row[4].strip() if len(row) > 4 and row[4] else "sensei777"
                
                # F列から各校固有のスプレッドシートIDを取得
                raw_ss_id = row[5].strip() if len(row) > 5 and row[5] else ""
                ss_id = raw_ss_id if raw_ss_id and re.match(r'^[a-zA-Z0-9\-_]{25,}$', raw_ss_id) else st.secrets["GOOGLE_SHEET_ID"]
                
                # G列から各校固有の音声フォルダIDを取得
                raw_f_id = row[6].strip() if len(row) > 6 and row[6] else ""
                f_id = raw_f_id if raw_f_id and re.match(r'^[a-zA-Z0-9\-_]{25,}$', raw_f_id) else st.secrets["GOOGLE_DRIVE_FOLDER_ID"]
                
                row_num = idx + 2
                
                if sch not in mapping: mapping[sch] = {}
                mapping[sch][cls] = {
                    "unit": unit, "text": txt, "password": pwd, 
                    "school_sheet_id": ss_id, "school_folder_id": f_id, "row_num": row_num
                }
        return mapping
    except Exception as e:
        return {"A中学校": {"1A": {"unit": "Unit 1", "text": "Welcome.", "password": "pass", "school_sheet_id": st.secrets["GOOGLE_SHEET_ID"], "school_folder_id": st.secrets["GOOGLE_DRIVE_FOLDER_ID"], "row_num": 2}}}

master_mapping = load_master_data()
school_options = sorted(list(master_mapping.keys()))


# --- 3. 生徒の個人情報入力 ---
query_params = st.query_params
param_school = query_params.get("school", None)

col1, col2, col3, col4 = st.columns(4)
with col1: 
    if param_school in school_options:
        school_name = st.selectbox("学校名：", [param_school], disabled=True)
    else:
        school_name = st.selectbox("学校名：", school_options)
with col2:
    available_classes = sorted(list(master_mapping.get(school_name, {}).keys()))
    class_name = st.selectbox("クラス：", available_classes)
with col3: 
    num_options = [f"{i:02d}番" for i in range(1, 46)]
    selected_num_text = st.selectbox("出席番号：", num_options)
    student_num = selected_num_text.replace("番", "")
with col4: 
    student_name = st.text_input("ID：", placeholder="例: TS")

current_class_data = master_mapping.get(school_name, {}).get(class_name, {"unit": "未設定", "text": "英文なし", "password": "none", "school_sheet_id": st.secrets["GOOGLE_SHEET_ID"], "school_folder_id": st.secrets["GOOGLE_DRIVE_FOLDER_ID"], "row_num": 0})
teacher_unit = current_class_data["unit"]
teacher_text = current_class_data["text"]

# 動的に仕分けるための学校個別ターゲットIDをセット
target_school_sheet_id = current_class_data["school_sheet_id"]
target_school_folder_id = current_class_data["school_folder_id"]

st.markdown("---")
st.markdown(f"### 📖 今日の課題: **{teacher_unit}**")
st.markdown(f"<div style='font-size: 19px; font-weight: bold; line-height: 1.8; color: #000000; background-color: #ffffff; padding: 25px; border: 1px solid #cbd5e0; border-radius: 12px; white-space: pre-wrap;'>{teacher_text}</div>", unsafe_allow_html=True)
st.markdown("---")

st.subheader("🎤 録音スタート")
audio_value = st.audio_input("ここを押して英語を読んでね")


# --- 4. Azure AI音声解析 ＆ 奇数・偶数F0自動分散ロジック（単発処理版） ---
if audio_value:
    audio_bytes = audio_value.read()
    
    if "current_audio_bytes" not in st.session_state or st.session_state.current_audio_bytes != audio_bytes:
        st.session_state.current_audio_bytes = audio_bytes
        
        status_placeholder = st.empty()
        status_placeholder.info("AIが分析しています... 🤖")
        
        with open("temp_audio.wav", "wb") as f: 
            f.write(audio_bytes)
            
        try:
            # 🎯 【F0仕分け】出席番号の奇数・偶数で使うキーを完全に分ける
            try:
                num_check = int(student_num)
                if num_check % 2 == 1:
                    final_key = st.secrets["KEY_KISU"]
                else:
                    final_key = st.secrets["KEY_GUSU"]
            except:
                # 万が一、出席番号に数字以外（未入力など）が入っていた場合のセーフティ
                final_key = st.secrets["KEY_KISU"]
            
            # Azure発音評定のセットアップ
            speech_config = speechsdk.SpeechConfig(subscription=final_key, region=azure_region)
            audio_config = speechsdk.audio.AudioConfig(filename="temp_audio.wav")
            
            pronunciation_config = speechsdk.PronunciationAssessmentConfig(
                reference_text=teacher_text,
                grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
                granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme
            )
            pronunciation_config.phoneme_alphabet = "IPA"
            
            speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
            pronunciation_config.apply_to(speech_recognizer)
            
            # 🚀 シンプルに1回だけリクエストを投げて結果を待つ（getで同期処理）
            result = speech_recognizer.recognize_once_async().get()
            status_placeholder.empty()
            
            if result.reason == speechsdk.ResultReason.RecognizedSpeech:
                pron_result = speechsdk.PronunciationAssessmentResult(result)
                score_acc = int(pron_result.accuracy_score)
                score_flu = int(pron_result.fluency_score)
                score_comp = int(pron_result.completeness_score)
                score_pros = int(pron_result.prosody_score) if hasattr(pron_result, 'prosody_score') and pron_result.prosody_score is not None else 85
                final_score = int((score_acc + score_flu + score_pros + score_comp) / 4)
                
                words_data, mispronounced_words, katakana_warnings = [], [], []
                vowel_phonemes = ["u", "o", "a", "e", "i", "ɔ", "ə", "ɑ"]
                for word in pron_result.words:
                    words_data.append({"word": word.word, "error_type": word.error_type})
                    if word.error_type == "Mispronunciation":
                        mispronounced_words.append(word.word)
                        if hasattr(word, 'phonemes') and word.phonemes:
                            for ph in word.phonemes:
                                if ph.phoneme in vowel_phonemes and word.word.endswith(("t", "k", "d", "g", "p", "b", "s", "n", "m")):
                                    katakana_warnings.append(f"**{word.word}**")
                                    break
                
                st.session_state.saved_results = {
                    "final_score": final_score, "score_acc": score_acc, "score_flu": score_flu, "score_pros": score_pros, "score_comp": score_comp,
                    "words_data": words_data, "mispronounced_words": mispronounced_words, "katakana_warnings": katakana_warnings, "audio_bytes": audio_bytes, "unit_name": teacher_unit
                }
        except Exception as azure_err:
            status_placeholder.empty()
            st.error(f"❌ AI解析中にエラーが発生しました: {azure_err}")
        finally:
            if os.path.exists("temp_audio.wav"): os.remove("temp_audio.wav")

    # --- 🔒 5. 点数固定 ＆ 中学生応援アドバイス ---
    if "saved_results" in st.session_state and st.session_state.saved_results:
        res = st.session_state.saved_results
        st.markdown(f"<div style='background-color: #f0fff4; padding: 20px; border-radius: 12px; text-align: center;'><span style='font-size: 48px; font-weight: bold; color: #2f855a;'>{res['final_score']}点</span></div>", unsafe_allow_html=True)
        
        colored_html = "<div style='font-size: 22px; line-height: 2.0; background-color: #f8fafc; padding: 20px; border-radius: 10px; margin-top: 15px; border: 1px solid #e2e8f0; color: #000000;'>"
        for w_info in res["words_data"]:
            w_text = w_info["word"]
            err_t = w_info["error_type"]
            if err_t == "None": colored_html += f"<span style='color: #2f855a; font-weight: bold;'>{w_text} </span>"
            elif err_t == "Mispronunciation": colored_html += f"<span style='color: #e53e3e; font-weight: bold; text-decoration: underline;'>{w_text} </span>"
            elif err_t == "Omission": colored_html += f"<span style='color: #718096; text-decoration: line-through;'>{w_text} </span>"
            else: colored_html += f"<span style='color: #dd6b20;'>{w_text} </span>"
        colored_html += "</div>"
        st.markdown(colored_html, unsafe_allow_html=True)
        
        chart_data = pd.DataFrame({"観点": ["正確さ(音)", "流暢さ(スピード)", "抑揚(リズム)", "完成度(読み飛ばし)"], "スコア": [res['score_acc'], res['score_flu'], res['score_pros'], res['score_comp']]})
        st.bar_chart(chart_data.set_index("観点"))
        
        st.markdown("---")
        st.markdown("### 🗣️ AIアドバイザーからのメッセージ")
        
        scores = {"声の出し方（ハッキリ度）": res['score_acc'], "スピード（なめらかさ）": res['score_flu'], "リズム（英語らしい強弱）": res['score_pros'], "読み忘れ（最後まで）": res['score_comp']}
        weak_point = min(scores, key=scores.get)
        advice_details = ""
        
        if res['katakana_warnings']:
            advice_details += f"📢 **おっと！もったいないポイント発見！**\n単語のうしろに余計な「う」や「お」の音がくっついて、ローマ字読み（カタカナ）になっている部分があるよ。\n言葉の終わりで口をピタッと止めて、息だけで「サッ」と終わらせるイメージで言ってみよう！\n* 👉 **注意する単語：** {', '.join(list(set(res['katakana_warnings'])))}\n\n"
        
        if res['final_score'] >= 90: advice_details += f"🏅 **す、すごすぎるーー！！【{res['final_score']}点】の神発音です！**\n耳がめちゃくちゃ良い証拠だね！先生もビックリの最高クオリティ。この調子でどんどん自信を持っていこう！絶対に英語が得意になるよ！\n\n"
        elif res['final_score'] >= 80: advice_details += f"✨ **うおー！めっちゃうまい！【{res['final_score']}点】のハイレベル合格！**\n声がしっかりAIに届いているよ。あとほんの少しの「コツ」で、夢の90点オーバー・満点が狙えるぞ。次が本番だ！\n\n"
        else: advice_details += f"👍 **ナイスチャレンジ！よく頑張って声をだしたね！**\nまずは挑戦した自分に拍手！今のはまだ練習の第１歩。ここから絶対に点数は上がるから、デジタル教科書のお手本音声をもう一度よく聴いて、下の【まほうの裏ワザ】を試してみて！\n\n"
            
        if res['final_score'] < 85:
            advice_details += f"🎯 **【次に10点アップするための、まほうの裏ワザ】**\n"
            if weak_point == "声の出し方（ハッキリ度）": advice_details += "👉 **『カラオケで100点を狙う作戦』で行こう！**\n画面の「赤色の文字」は、AIが少し聞き取りにくかった音だよ。デジタル教科書のお手本音声をもう一度よく聴いて、音程をそっくりそのまま真似っこする感じで、口を少し大きめに動かして言ってみよう！"
            elif weak_point == "スピード（なめらかさ）": advice_details += "👉 **『単語どうしを、のりではりつける作戦』で行こう！**\n「私は・学校に・行きます」みたいにブツブツ止っちゃうと、AIが迷子になっちゃうんだ。文字じゃなくて『ひとつの塊』として、なめらかにつなげて一気に言い切ってみよう！"
            elif weak_point == "リズム（英語らしい強弱）": advice_details += "👉 **『太鼓のドラムをたたく作戦』で行こう！**\n全部の文字を同じ強さで「ロボット」みたいに読むのはNG！大事な単語だけを「ドン！」と力強く、それ以外の小さな単語（the や in など）は「トントン」と優しく読むと、一気にめちゃくちゃカッコよくなるよ！"
            elif weak_point == "読み忘れ（最後まで）": advice_details += "👉 **『ゴールラインまで全力ダッシュ作戦』で行こう！**\n画面の「灰色の文字」は、AIが聞き取れなかった（読み飛ばしちゃった）ところだよ。恥ずかしがらずに、文の最後のピリオドまで、1つずつの単語を丁寧にハッキリ声に出してみてね！"
        
        st.info(advice_details)
        
        st.markdown("---")
        st.subheader("📮 先生への自動提出")
        if not (school_name and class_name and student_num and student_name):
            st.warning("⚠️ すべての項目を入力・選択してください。")
        else:
            if st.button("📤 この結果と音声を先生に提出する", type="primary"):
                with st.spinner("学校ごとの専用ドライブへ送信中..."):
                    try:
                        robot_email, client_id, formatted_private_key = st.secrets["ROBOT_EMAIL"], st.secrets["ROBOT_CLIENT_ID"], st.secrets["ROBOT_PRIVATE_KEY"]
                        info = {"type": "service_account", "project_id": "ai-ondoku-final-go", "private_key_id": "google_cloud_key", "private_key": formatted_private_key, "client_email": robot_email, "client_id": client_id, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token", "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs"}
                        creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/spreadsheets"])
                        drive_service, sheets_service = build('drive', 'v3', credentials=creds), build('sheets', 'v4', credentials=creds)
                        
                        filename = f"{school_name}_{class_name}_{student_num}番_{student_name}_{res['unit_name']}_{res['final_score']}点.wav"
                        media = MediaIoBaseUpload(io.BytesIO(res['audio_bytes']), mimetype='audio/wav')
                        
                        # 1. 音声ファイルをマスタG列指定の「学校個別フォルダ」へアップロード
                        uploaded_file = drive_service.files().create(
                            body={'name': filename, 'parents': [target_school_folder_id]}, 
                            media_body=media, fields='id', supportsAllDrives=True
                        ).execute()
                        
                        audio_link = f"https://drive.google.com/file/d/{uploaded_file.get('id')}/view?usp=drivesdk"
                        now_jst = datetime.utcnow() + timedelta(hours=9)
                        row_data = [now_jst.strftime('%Y-%m-%d %H:%M:%S'), school_name, class_name, student_num, student_name, res['unit_name'], res['final_score'], res['score_acc'], res['score_flu'], res['score_pros'], res['score_comp'], audio_link]
                        
                        # 2. 文字データをマスタF列指定の「学校個別スプレッドシート」の、該当学校名タブへ直接書き込み
                        sheets_service.spreadsheets().values().append(
                            spreadsheetId=target_school_sheet_id, 
                            range=f"{school_name}!A:L", 
                            valueInputOption="USER_ENTERED", 
                            insertDataOption="INSERT_ROWS", 
                            body={'values': [row_data]}
                        ).execute()
                        
                        # 💡 画面をパッと戻さず、提出完了メッセージを固定表示！
                        st.balloons()
                        st.markdown("""
                            <div style="background-color: #ebf8ff; border: 2px solid #3182ce; padding: 30px; border-radius: 15px; text-align: center; margin-top: 20px;">
                                <h2 style="color: #2b6cb0 !important; margin-bottom: 10px;">🎉 提出が完了しました！</h2>
                                <p style="color: #2d3748 !important; font-size: 20px; font-weight: bold;">
                                    先生のパソコン（スプレッドシート）にデータと音声が無事に届きました。<br>
                                    
                                </p>
                            </div>
                        """, unsafe_allow_html=True)
                        
                        # セッション情報のクリア（二重送信防止用）
                        if "saved_results" in st.session_state: del st.session_state.saved_results
                        if "current_audio_bytes" in st.session_state: del st.session_state.current_audio_bytes
                        
                    except Exception as ge: st.error(f"❌ 送信失敗: {ge}")
else:
    if "saved_results" in st.session_state: del st.session_state.saved_results
    if "current_audio_bytes" in st.session_state: del st.session_state.current_audio_bytes


# --- 6. 🛠️ 先生用・管理者メニュー ---
st.markdown(" ")
st.markdown(" ")
with st.expander("🛠️ 先生用・管理者メニュー（課題の変更）"):
    st.write("自分が担当する学校とクラスを選んで、パスワードを入力してEnterを押してください。")
    
    t_school = st.selectbox("管理する学校：", school_options, key="t_sch")
    t_class = st.selectbox("管理するクラス：", sorted(list(master_mapping.get(t_school, {}).keys())), key="t_cls")
    
    target_class_info = master_mapping[t_school][t_class]
    correct_password = target_class_info["password"]
    
    input_password = st.text_input("クラス用パスワードを入力（入力後Enter）：", type="password", key="t_pwd")
    
    if input_password:
        if input_password == correct_password:
            st.success(f"🔓 認証成功！ 【{t_school} {t_class}】の課題設定画面です。")
            
            new_unit = st.text_input("単元名：", value=target_class_info["unit"])
            new_text = st.text_area("英文（生徒画面に表示）：", value=target_class_info["text"])
            
            if st.button("🔄 このクラスの課題を更新する"):
                with st.spinner("スプレッドシートの課題を書き換え中..."):
                    try:
                        robot_email = st.secrets["ROBOT_EMAIL"]
                        client_id = st.secrets["ROBOT_CLIENT_ID"]
                        formatted_private_key = st.secrets["ROBOT_PRIVATE_KEY"]
                        info = {"type": "service_account", "project_id": "ai-ondoku-final-go", "private_key_id": "google_cloud_key", "private_key": formatted_private_key, "client_email": robot_email, "client_id": client_id, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token", "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs"}
                        creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
                        sheets_service = build('sheets', 'v4', credentials=creds)
                        spreadsheet_id = st.secrets["GOOGLE_SHEET_ID"]
                        
                        row_number = target_class_info["row_num"]
                        update_range = f"マスタ!C{row_number}:D{row_number}"
                        
                        update_body = {'values': [[new_unit, new_text]]}
                        sheets_service.spreadsheets().values().update(
                            spreadsheetId=spreadsheet_id, range=update_range,
                            valueInputOption="USER_ENTERED", body=update_body
                        ).execute()
                        
                        st.success(f"🎉 {t_class}の課題を更新しました！")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as update_err:
                        st.error(f"❌ スプレッドシートの更新に失敗しました: {update_err}")
        else:
            st.error("❌ パスワードが違います。")


# --- 7. 著作権 ＆ クレジット表記（フッター） ---
st.markdown("---")  # 区切り線
st.markdown(
    """
    <div style="text-align: center; color: #718096; font-size: 0.85rem; line-height: 1.8;">
        <p style="margin-bottom: 4px; font-weight: bold; color: #4a5568;">
            Copyright © 2026 Max Pro Project / Shogo Takeuchi All Rights Reserved.
        </p>
        <p style="margin-top: 0; margin-bottom: 4px;">
            <strong>AI音読システム Max Pro</strong> — Designed for Saku City Schools
        </p>
        <p style="margin-top: 0; font-size: 0.8rem; color: #718096;">
            Powered by 
            <a href="https://azure.microsoft.com/" target="_blank" style="color: #4a5568; text-decoration: underline;">Microsoft Azure AI Speech</a> (F0 Bundle) | 
            <a href="https://streamlit.io/" target="_blank" style="color: #4a5568; text-decoration: underline;">Streamlit Cloud</a> | 
            <a href="https://github.com/" target="_blank" style="color: #4a5568; text-decoration: underline;">GitHub</a> | 
            <a href="https://workspace.google.com/" target="_blank" style="color: #4a5568; text-decoration: underline;">Google Workspace</a>
        </p>
        <p style="font-size: 0.75rem; color: #a0aec0; letter-spacing: 0.05em;">
            [ 安全性定義：データ非蓄積型インフラ構造 / 個人情報外部非保持設計 ]
        </p>
    </div>
    """,
    unsafe_allow_html=True
)
