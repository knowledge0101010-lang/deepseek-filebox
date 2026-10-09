# ByteDance UI-TARS / Agent TARS 연동

참조 구현: **[bytedance/UI-TARS-desktop](https://github.com/bytedance/UI-TARS-desktop)**
(Apache-2.0) — "The Open-Source Multimodal AI Agent Stack".
[README](https://github.com/bytedance/UI-TARS-desktop/blob/main/README.md) ·
[MCP 가이드](https://agent-tars.com/guide/basic/mcp.html)

## 왜 이 연동인가

UI-TARS-desktop 계열은 두 가지를 제공한다.

| 구성 | 특징 | 이 저장소와의 접점 |
|---|---|---|
| **Agent TARS** | CLI + Web UI, **커널이 MCP 기반**, MCP 서버를 마운트해 실제 도구에 연결, headless 서버 실행 | 파일함을 **MCP 서버**로 노출하면 그대로 마운트된다 |
| **UI-TARS Desktop** | 비전-언어 모델 기반 GUI 에이전트, **Local / Remote Computer Operator**, 로컬 처리 | 원격 오퍼레이터로 다른 머신의 합본 작업을 조작 |

핵심은 MCP다. 이 저장소는 **표준 라이브러리만으로 MCP stdio 서버**(`tools/filebox_mcp.py`)를
제공하므로, Node.js 22+ 환경에서 TARS를 띄우고 이 서버를 마운트하면 원격 에이전트가
합본 상태를 조회·검증할 수 있다.

## 노출되는 MCP 도구

| 도구 | 하는 일 |
|---|---|
| `filebox_stats` | 항목 수·고유 내용·중복 그룹·바이너리 회피량 |
| `filebox_list` | tag/kind/search/limit 로 색인 조회 |
| `filebox_cat` | 저장된 원문 반환(바이너리는 실패 처리) |
| `filebox_verify` | **원본 재독 sha256 대조**(checked/exact/mismatch/ratio) |
| `filebox_dedupe` | 최대 중복 그룹 상위 N |

## 사용

### 1. 서버 단독 실행 (프로토콜 확인)

```powershell
# 새니티 체크: initialize + tools/list 를 한 줄씩 넣는다
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python tools/filebox_mcp.py --db .\box.db
```

### 2. Agent TARS 에 마운트

`agent-tars` 는 MCP 서버 설정을 받는다(문서: agent-tars.com/guide/basic/mcp.html).
stdio 서버이므로 command/args 형태로 등록한다:

```jsonc
{
  "mcpServers": {
    "deepseek-filebox": {
      "command": "python",
      "args": ["C:\\Users\\knowl\\OneDrive\\문서\\deepseek-harness\\deepseek-filebox\\tools\\filebox_mcp.py",
               "--db",
               "C:\\Users\\knowl\\OneDrive\\문서\\deepseek-harness\\DEEPSEEK-FILEBOX.db"]
    }
  }
}
```

```bash
npx @agent-tars/cli@latest        # Node.js >= 22
```

### 3. UI-TARS Desktop 원격 오퍼레이터

원격 조작 대상 머신에 이 저장소를 두고, 그 머신에서 `python tools/filebox_mcp.py` 가
돌 수 있게 한다. UI-TARS Desktop 의 **Remote Computer Operator** 로 그 머신을 잡으면
GUI 조작과 함께 MCP 도구 호출이 가능하다. 로컬 처리 원칙(README 명시)에 따라 파일
내용은 로컬에서 읽고, 모델 호출만 원격으로 나간다.

## 정직한 한계

1. 이 저장소는 UI-TARS/TARS 코드를 포함하거나 재배포하지 않는다. **MCP라는 공개 프로토콜로만**
   접점을 만든다.
2. 여기 구현한 MCP 서버는 **읽기 전용**이다(조회·검증만). 쓰기 도구는 의도적으로 없다.
3. UI-TARS 측 통합 테스트는 이 저장소 CI에 없다(Node.js 22+ 및 모델 자격증명 필요).
   CI가 검증하는 것은 **우리 MCP 서버의 프로토콜 동작**까지다(`tests/test_mcp.py`).
4. "원격 조작"은 그 자체로 위험하다. 원격 오퍼레이터에 연결할 때는 대상 머신을 격리하고,
   무엇을 조작하게 할지 사람이 승인하는 절차를 두는 것을 권한다.
