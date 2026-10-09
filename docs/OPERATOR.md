# Local Operator — 마우스·키보드 + 화면 캡처 (MCP)

에이전트가 **이 컴퓨터를 직접 조작**할 수 있게 하는 계층입니다. 참조: ByteDance UI-TARS
Desktop 의 Local/Remote Computer Operator — 스크린샷 + 시각 접지 + 마우스/키보드 동작.
([UI-TARS-desktop](https://github.com/bytedance/UI-TARS-desktop))

표준 라이브러리만 사용합니다(ctypes + Windows GDI). PNG 인코더도 직접 구현했습니다.

## 안전 모델 (먼저 읽으세요)

| 장치 | 기본값 | 설명 |
|---|---|---|
| 입력 활성화 | **꺼짐** | `--enable-input` 없이는 `mouse_*`/`key_*` 가 **거부**됩니다 |
| dry-run | **켜짐**(입력 비활성 시) | 실행 계획만 돌려주고 아무것도 하지 않습니다 |
| 좌표 검사 | 항상 | 실제 화면 크기 밖 좌표는 거부 |
| 속도 제한 | 초당 20회 | 연속 조작 폭주 방지 |
| 감사 로그 | 선택(`--audit`) | 시도 **전에** JSONL로 기록 |
| 킬 스위치 | `operator_panic` | 세션 전체에서 입력 잠금 |

```powershell
python tools/operator_mcp.py                    # 안전: 비활성 + dry-run
python tools/operator_mcp.py --dry-run          # 명시적 dry-run
python tools/operator_mcp.py --enable-input --audit .\operator-audit.jsonl   # 실제 입력 무장
```

## 노출 도구 (MCP)

| 도구 | 인자 | 하는 일 |
|---|---|---|
| `operator_status` | — | 플랫폼·화면 크기·입력 활성·dry-run·감사 경로 |
| `screen_capture` | `max_width`(기본 1600) | 데스크톱을 PNG(base64)로. 정수 스텝으로 축소 |
| `mouse_move` | `x`,`y` | 커서 이동(경계 검사) |
| `mouse_click` | `x?`,`y?`,`button`,`count` | 클릭. 좌표 생략 시 현재 위치 |
| `key_type` | `text` | 유니코드 키 이벤트로 문자열 입력 |
| `key_press` | `keys:["CTRL","S"]` | 키 조합(A–Z, 0–9, ENTER/TAB/ESC/F1–F12 등) |
| `operator_panic` | — | 입력 잠금(킬 스위치) |

## 새니티 체크

```powershell
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python tools/operator_mcp.py
'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"operator_status","arguments":{}}}' | python tools/operator_mcp.py
```

기본 모드에서는 `mouse_move` 가 이렇게 답합니다:

```json
{"ok": false, "error": "input disabled: start the server with --enable-input"}
```

## UI-TARS / Agent TARS 와 연결

```jsonc
{
  "mcpServers": {
    "deepseek-operator": {
      "command": "python",
      "args": ["...\\deepseek-filebox\\tools\\operator_mcp.py",
               "--enable-input",
               "--audit", "...\\operator-audit.jsonl"]
    }
  }
}
```

원격 오퍼레이터로 쓸 때는 **대상 머신에서** 이 서버를 띄우고, 그 머신을 UI-TARS Desktop 의
Remote Computer Operator 로 잡습니다. 파일 내용은 로컬에서 읽고 모델 호출만 밖으로 나갑니다.

## 테스트가 증명하는 것

`tests/test_operator.py` (17개 중 8개) 는 **실제 커서를 움직이지 않습니다**:

- 기본값이 입력을 거부하는가
- dry-run 이 계획만 돌려주는가 (`applied: false`)
- 화면 밖 좌표를 거부하는가
- 잘못된 버튼/키/개수를 거부하는가
- `operator_panic` 후 모든 입력이 잠기는가
- 감사 로그가 시도 전에 기록되는가
- MCP 기본 모드에서 입력이 거부되는가

## 한계 (정직)

1. **Windows 전용**: 화면 캡처·입력은 `user32`/`gdi32`. 다른 OS에서는 `unsupported platform`
   을 돌려주고 dry-run 만 동작합니다(CI 는 Linux 에서 그 경로를 검증).
2. **시각 접지 없음**: "저 버튼을 눌러라"를 스스로 판단하지 않습니다. 좌표를 주는 것은
   모델(예: UI-TARS)의 몫입니다. 이 계층은 손과 눈만 제공합니다.
3. **UI-TARS 앱과의 종단 통합 테스트는 이 저장소 CI 에 없습니다**(Node 22+ 및 모델 자격증명 필요).
   CI 가 검증하는 것은 우리 MCP 프로토콜과 안전 장치까지입니다.
4. **원격 조작은 그 자체로 위험합니다.** 격리된 대상 머신에서만, 사람의 승인 절차와 함께
   사용하세요. 감사 로그를 켜지 않은 채로 `--enable-input` 을 쓰지 마세요.
