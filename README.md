# 🛒 쇼핑 리스트

아이템 추가, 수정, 삭제, 체크 기능이 있는 간단한 Streamlit 웹 앱입니다. 요구사항은 [PRD.md](PRD.md)를 참고하세요.

## 로컬 실행

```bash
pip install -r requirements.txt
streamlit run app.py
```

브라우저에서 http://localhost:8501 로 접속합니다.

목록은 `shopping_list.json` 파일에 저장되어 새로고침하거나 앱을 다시 켜도 유지됩니다.

## 테스트 (Playwright)

```bash
pip install pytest playwright pytest-playwright
playwright install chromium
pytest tests/                                # 전체
pytest tests/test_app.py::test_add_item      # 하나만
pytest tests/ --headed                       # 브라우저를 띄워서 보기
```

테스트는 임시 데이터 파일을 쓰므로 실제 목록에는 영향을 주지 않습니다.
