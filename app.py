import streamlit as st
import azure.cognitiveservices.speech as speechsdk
import os
import io
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import pandas as pd

st.set_page_config(page_title="AI音読アドバイザー Max Pro", layout="centered")

# --- 🎨 画面のデザイン設定 ---
st.markdown("""
    <style>
    /* 全体の基本設定 */
    .stApp { background-color: #ffffff; color: #1a202c; }
    h1, h2, h3 { color: #1a365d !important; font-weight: 700; }
    p, li, label, .stMarkdown { color: #2d3748 !important; font-size: 18px; line-height: 1.6; }
    
    /* ⭕ プルダウンを閉じてる時の枠（白背景に黒文字で強制固定） */
    .stSelectbox>div>div>div, .stTextInput>div>div>input, .stTextArea>div>textarea {
        background-color: #ffffff !important; 
        color: #000000 !important;
        border: 2px solid #cbd5e0 !important; 
        border-radius: 8px !important;
    }
    
    /* ⭕ プルダウンを開いた時の「選択肢のリスト全体の背景」を真っ白に強制固定 */
    div[data-baseweb="popover"] {
        background-color: #ffffff !important;
    }
    div[data-baseweb="popover"] ul {
        background-color: #ffffff !important;
    }
    
    /* ⭕ 選択肢の「1文字1文字」を真っ黒に強制固定 */
    div[data-baseweb="popover"] li {
        color: #000000 !important; 
        background-color: #ffffff !important;
    }
    
    /* ⭕ マウスを乗せたり、スマホでタップした選択肢の背景を「薄いグレー」にする */
    div[data-baseweb="popover"] li:hover {
        background-color: #edf2f7 !important;
        color: #000000 !important;
    }

    .stAudioInput { background-color: #f8fafc; border-radius: 12px; padding: 10px; border: 1px solid #e2e8f0; }
    </style>
""", unsafe_allow_html=True)
st.title("🗣️ AI音読システム Max Pro")
st.write("画面に表示されている英文を読んで、録音して提出しよう！")

# --- 1. 出席番号・班による負荷分散 ---
attendance_type = st.radio(
    "あなたの 出席番号（または班） を選んでください：",
    ["奇数番号 (1, 3, 5...)", "偶数番号 (2, 4, 6...)"],
    horizontal=True
)

if "奇数" in attendance_type:
    azure_key = st.secrets["KEY_KISU"]
else:
    azure_key = st.secrets["KEY_GUSU"]

azure_region = st.secrets["AZURE_REGION"]


# --- 2. Googleスプレッドシートからマスタを取得する機能 ---
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
        
        result = sheets_service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range="マスタ!A2:F200").execute()
        rows = result.get('values', [])
        
        mapping = {}
        for idx, row in enumerate(rows):
            if len(row) >= 2 and row[0] and row[1]:
                sch = row[0].strip()
                cls = row[1].strip()
                unit = row[2].strip() if len(row) > 2 and row[2] else "課題"
                txt = row[3].strip() if len(row) > 3 and row[3] else "English text here."
                pwd = row[4].strip() if len(row) > 4 and row[4] else "sensei777"
                file_id = row[5].strip() if len(row) > 5 and row[5] else "記入不要"
                
                row_num = idx + 2
                
                if sch not in mapping: mapping[sch] = {}
                mapping[sch][cls] = {"unit": unit, "text": txt, "password": pwd, "row_num": row_num}
        return mapping
    except Exception as e:
        return {"A中学校": {"1A": {"unit": "Unit 1", "text": "Welcome to school.", "password": "pass", "row_num": 2}}}

master_mapping = load_master_data()
school_options = sorted(list(master_mapping.keys()))


# --- 3. 生徒の個人情報入力 ---
col1, col2, col3, col4 = st.columns(4)
with col1: school_name = st.selectbox("学校名：", school_options)
with col2:
    available_classes = sorted(list(master_mapping.get(school_name, {}).keys()))
    class_name = st.selectbox("クラス：", available_classes)
with col3: student_num = st.text_input("出席番号：", placeholder="例: 05")
with col4: student_name = st.text_input("イニシャル：", placeholder="例: AT")

current_class_data = master_mapping.get(school_name, {}).get(class_name, {"unit": "未設定", "text": "英文が登録されていません。", "password": "none", "row_num": 0})
teacher_unit = current_class_data["unit"]
teacher_text = current_class_data["text"]

st.markdown("---")
st.markdown(f"### 📖 今日の課題: **{teacher_unit}**")
st.markdown(f"<div style='font-size: 19px; font-weight: bold; line-height: 1.8; color: #000000; background-color: #ffffff; padding: 25px; border: 1px solid #cbd5e0; border-radius: 12px; white-space: pre-wrap;'>{teacher_text}</div>", unsafe_allow_html=True)
st.markdown("---")


# --- 4. 🎧 AIお手本音声の再生機能 ---
with st.expander("🎧 AIのお手本音声を聴く"):
    if st.button("🔊 お手本を再生する"):
        with st.spinner("AI音声を生成中..."):
            try:
                speech_config = speechsdk.SpeechConfig(subscription=azure_key, region=azure_region)
                speech_config.speech_synthesis_voice_name = "en-US-JennyNeural"
                speech_synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
                result = speech_synthesizer.speak_text_async(teacher_text).get()
                if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                    st.audio(result.audio_data, format="audio/wav", autoplay=True)
            except Exception as tts_error: st.error(f"エラー: {tts_error}")


st.subheader("🎤 録音スタート")
audio_value = st.audio_input("ここを押して英語を読んでね")


# --- 5. Azure AI音声解析＆カタカナ検知ロジック ---
if audio_value:
    audio_bytes = audio_value.read()
    
    if "current_audio_bytes" not in st.session_state or st.session_state.current_audio_bytes != audio_bytes:
        st.session_state.current_audio_bytes = audio_bytes
        st.info("AIが分析中... 🤖")
        with open("temp_audio.wav", "wb") as f: f.write(audio_bytes)
        try:
            speech_config = speechsdk.SpeechConfig(subscription=azure_key, region=azure_region)
            audio_config = speechsdk.audio.AudioConfig(filename="temp_audio.wav")
            pronunciation_config = speechsdk.PronunciationAssessmentConfig(json_string=f'{{"referenceText":"{teacher_text}","gradingSystem":"HundredMark","granularity":"Phoneme","phonemeAlphabet":"IPA"}}')
            pronunciation_config.enable_prosody_assessment()
            speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
            pronunciation_config.apply_to(speech_recognizer)
            result = speech_recognizer.recognize_once_async().get()
            
            if result.reason == speechsdk.ResultReason.RecognizedSpeech:
                pron_result = speechsdk.PronunciationAssessmentResult(result)
                score_acc = int(pron_result.accuracy_score)
                score_flu = int(pron_result.fluency_score)
                score_comp = int(pron_result.completeness_score)
                score_pros = int(pron_result.prosody_score) if hasattr(pron_result, 'prosody_score') else 85
                final_score = int((score_acc + score_flu + score_pros + score_comp) / 4)
                
                words_data, mispronounced_words, katakana_warnings = [], [], []
                vowel_phonemes = ["u", "o", "a", "e", "i", "ɔ", "ə", "ɑ"]
                for word in pron_result.words:
                    words_data.append({"word": word.word, "error_type": word.error_type})
                    if word.error_type == "Mispronunciation":
                        mispronounced_words.append(word.word)
                        if hasattr(word, 'phonemes'):
                            for ph in word.phonemes:
                                if ph.phoneme in vowel_phonemes and word.word.endswith(("t", "k", "d", "g", "p", "b", "s", "n", "m")):
                                    katakana_warnings.append(f"**{word.word}**")
                                    break
                
                st.session_state.saved_results = {
                    "final_score": final_score, "score_acc": score_acc, "score_flu": score_flu, "score_pros": score_pros, "score_comp": score_comp,
                    "words_data": words_data, "mispronounced_words": mispronounced_words, "katakana_warnings": katakana_warnings, "audio_bytes": audio_bytes, "unit_name": teacher_unit
                }
        finally:
            if os.path.exists("temp_audio.wav"): os.remove("temp_audio.wav")

    # 固定された結果を画面に表示する
    if "saved_results" in st.session_state and st.session_state.saved_results:
        res = st.session_state.saved_results
        st.markdown(f"<div style='background-color: #f0fff4; padding: 20px; border-radius: 12px; text-align: center;'><span style='font-size: 48px; font-weight: bold; color: #2f855a;'>{res['final_score']}点</span></div>", unsafe_allow_html=True)
        
        # ⭕【復活！】単語ごとの赤字・緑字カラー判定＆表示システム
        colored_html = "<div style='font-size: 22px; line-height: 2.0; background-color: #f8fafc; padding: 20px; border-radius: 10px; margin-top: 15px; border: 1px solid #e2e8f0; color: #000000;'>"
        for w_info in res["words_data"]:
            w_text = w_info["word"]
            err_t = w_info["error_type"]
            if err_t == "None":
                colored_html += f"<span style='color: #2f855a; font-weight: bold;'>{w_text} </span>"  # 正解は緑
            elif err_t == "Mispronunciation":
                colored_html += f"<span style='color: #e53e3e; font-weight: bold; text-decoration: underline;'>{w_text} </span>"  # ミスは赤＋下線
            elif err_t == "Omission":
                colored_html += f"<span style='color: #718096; text-decoration: line-through;'>{w_text} </span>"  # 読み飛ばしは灰色＋打ち消し線
            else:
                colored_html += f"<span style='color: #dd6b20;'>{w_text} </span>"  # その他はオレンジ
        colored_html += "</div>"
        st.markdown(colored_html, unsafe_allow_html=True)
        
        chart_data = pd.DataFrame({"観点": ["正確さ(音)", "流暢さ(スピード)", "抑揚(リズム)", "完成度(読み飛ばし)"], "スコア": [res['score_acc'], res['score_flu'], res['score_pros'], res['score_comp']]})
        st.bar_chart(chart_data.set_index("観点"))
        
        advice_text = ""
        if res['katakana_warnings']: advice_text += f"💡 **カタカナ英語注意！** 最後は母音をつけずに子音だけで止める意識を！\n* 👉 {', '.join(list(set(res['katakana_warnings'])))}\n\n"
        if res['final_score'] >= 85: advice_text += "🎯 すばらしい発音です！"
        else: advice_text += "👍 ナイスチャレンジ！赤色の単語を聞き直してみよう。"
        st.info(advice_text)
        
        st.markdown("---")
        st.subheader("📮 先生への自動提出")
        if not (school_name and class_name and student_num and student_name):
            st.warning("⚠️ すべての項目を入力・選択してください。")
        else:
            if st.button("📤 この結果と音声を先生に提出する", type="primary"):
                with st.spinner("送信中..."):
                    try:
                        robot_email, client_id, formatted_private_key = st.secrets["ROBOT_EMAIL"], st.secrets["ROBOT_CLIENT_ID"], st.secrets["ROBOT_PRIVATE_KEY"]
                        info = {"type": "service_account", "project_id": "ai-ondoku-final-go", "private_key_id": "google_cloud_key", "private_key": formatted_private_key, "client_email": robot_email, "client_id": client_id, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token", "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs"}
                        creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/spreadsheets"])
                        drive_service, sheets_service = build('drive', 'v3', credentials=creds), build('sheets', 'v4', credentials=creds)
                        
                        folder_id, spreadsheet_id = st.secrets["GOOGLE_DRIVE_FOLDER_ID"], st.secrets["GOOGLE_SHEET_ID"]
                        filename = f"{school_name}_{class_name}_{student_num}番_{student_name}_{res['unit_name']}_{res['final_score']}点.wav"
                        media = MediaIoBaseUpload(io.BytesIO(res['audio_bytes']), mimetype='audio/wav')
                        uploaded_file = drive_service.files().create(body={'name': filename, 'parents': [folder_id]}, media_body=media, fields='id', supportsAllDrives=True).execute()
                        
                        audio_link = f"https://drive.google.com/file/d/{uploaded_file.get('id')}/view?usp=drivesdk"
                        now_jst = datetime.utcnow() + timedelta(hours=9)
                        row_data = [now_jst.strftime('%Y-%m-%d %H:%M:%S'), school_name, class_name, student_num, student_name, res['unit_name'], res['final_score'], res['score_acc'], res['score_flu'], res['score_pros'], res['score_comp'], audio_link]
                        
                        sheets_service.spreadsheets().values().append(spreadsheetId=spreadsheet_id, range=f"{school_name}!A:L", valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS", body={'values': [row_data]}).execute()
                        st.balloons(); st.success("🎉 提出が完了しました！")
                        
                        if "saved_results" in st.session_state: del st.session_state.saved_results
                        if "current_audio_bytes" in st.session_state: del st.session_state.current_audio_bytes
                        st.rerun()
                    except Exception as ge: st.error(f"❌ 送信失敗: {ge}")
else:
    if "saved_results" in st.session_state: del st.session_state.saved_results
    if "current_audio_bytes" in st.session_state: del st.session_state.current_audio_bytes


# --- 6. 🛠️ 先生用・管理者メニュー（課題の変更） ---
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
