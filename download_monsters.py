"""몬스터 이미지 다운로드 — id_한글이름(속성).webp"""
import urllib.request
import json
import os
import re

ELEMENT_MAP = {1: "火", 2: "水", 3: "風", 4: "雷"}
API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Iml2ZWRybXNoZXF0bHJ4eXR6ZnFkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzEwNjg0NjEsImV4cCI6MjA4NjY0NDQ2MX0.Wlet2liCSQaWDjLl9lloiJbubDSPip2Yc5muwhB0gKA"

# 1. 전체 몬스터 데이터 가져오기 (속성 포함)
url = "https://ivedrmsheqtlrxytzfqd.supabase.co/rest/v1/monster?select=id,name,element_type,element_value,image_url&limit=10000"
req = urllib.request.Request(url, headers={
    "apikey": API_KEY,
    "Authorization": f"Bearer {API_KEY}"
})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode("utf-8"))

# JSON 저장
with open("monster_data.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"총 {len(data)}개 몬스터 데이터 저장")

# 2. 기존 파일 삭제
img_dir = "monster_images"
os.makedirs(img_dir, exist_ok=True)
for old in os.listdir(img_dir):
    os.remove(os.path.join(img_dir, old))

# 3. 이미지 다운로드 (속성 한자 포함)
base = "https://ivedrmsheqtlrxytzfqd.supabase.co/storage/v1/object/public/image/"
ok = 0
fail = 0

for m in data:
    mid = m["id"]
    name = m["name"]
    elem = m.get("element_type")

    # 속성 한자 붙이기
    if elem and elem in ELEMENT_MAP:
        display_name = f"{name}({ELEMENT_MAP[elem]})"
    else:
        display_name = name

    url = base + m["image_url"]
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', display_name)
    path = os.path.join(img_dir, f"{mid}_{safe_name}.webp")

    try:
        urllib.request.urlretrieve(url, path)
        ok += 1
        if ok % 100 == 0:
            print(f"  {ok}/{len(data)}...")
    except Exception:
        fail += 1

print(f"완료: {ok} 성공, {fail} 실패")
