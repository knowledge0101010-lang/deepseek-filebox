# VLA 실행 계획 — 유니트리 56개 저장소 + 실측 수치

작성: 2026-10-09 (Asia/Seoul) · DSH · 모든 수치는 이 기계에서 실측했거나 출처를 밝힌 시장가입니다.

## 0. 한 줄 결론

**이 노트북에서는 VLA 학습이 불가능합니다**(CUDA GPU 없음). 다만 **데이터·코드·파이프라인은 이미
전부 확보**했고, **대여 GPU에서 LoRA 파인튜닝까지는 실현 가능**합니다. 유튜브 영상은
**행동 정답이 없으므로** 그대로는 로봇 행동 학습에 쓸 수 없습니다 — 인터넷 데이터는 VLM
사전학습(눈)에, 로봇 시연 데이터는 정책 학습(손)에 씁니다.

## 1. 이 기계의 실측 능력 (하드 리밋)

| 항목 | 실측값 | VLA 학습 요구 | 판정 |
|---|---|---|---|
| GPU | Intel Iris Xe (내장, 공유 2GB) | NVIDIA A100/H100 40–80GB | ❌ |
| CUDA | `torch 2.13.0+cpu`, `cuda False` | CUDA 12.x | ❌ |
| CPU | i5-1334U (10C/12T, ULV) | 데이터 로더용 32코어+ | ⚠️ 부족 |
| RAM | 15.7 GB (여유 3.5 GB) | 64–512 GB | ❌ |
| 디스크 | C: 475.9 GB (여유 93.3 GB) | 데이터 100 GB+ 여유 | ⚠️ 빠듯 |
| OS | Windows 11 Pro 26200 | Linux + NCCL | ⚠️ WSL 필요 |

→ **로컬 학습 0%, 원격 GPU 100%** 구조로 가야 합니다.

## 2. 확보된 것 (실측)

| 자산 | 수치 |
|---|---|
| 유니트리 저장소 | **56개 전부 수집** (4,678 MB) |
| **UNITREE-FILEBOX.db** | **82.3 MB** · 항목 8,071 · 저장 5,689 · **고유 4,597개 / 74.91 MB** |
| 바이너리 | 2,382개 / **1,972.8 MB** → 지문만 저장(내용 미보관) |
| VLA 파이프라인 | `unitree_IL_lerobot` pi05 역공학 **19/19 PASS** (PaliGemma 2B + action expert 300M, flow matching) |
| HF 데이터셋 | `unitreerobotics` 명의 **100개** (로봇 시연 데이터) |

### 대표 데이터셋 실측 크기 (HuggingFace API)

| 데이터셋 | 총량 | 영상 | 데이터(parquet) |
|---|---|---|---|
| G1_Dex3_ToastedBread | **13.83 GB** | 13.75 GB | 0.07 GB |
| G1_Dex3_BlockStacking | 8.55 GB | 8.49 GB | 0.06 GB |
| Z1_Dual_Dex1_FoldClothes | 5.78 GB | 5.76 GB | 0.02 GB |
| R1_Dex1_Fold_Towel | 1.34 GB | 1.33 GB | 0.00 GB |
| unitree_sim_isaaclab_usds | 1.22 GB | – | – |

→ 학습에 필요한 **행동 라벨은 parquet(수십 MB)에** 있고, 용량 대부분은 **관측 영상**입니다.
즉 "영상 13.8 GB 중 행동 정답은 70 MB" — 유튜브 영상이 행동 학습에 못 쓰이는 이유와 같은 구조입니다.

## 3. 실행 경로 (우선순위)

### 경로 A — LoRA 파인튜닝 (현실적, 권장)
| 항목 | 값 |
|---|---|
| 대상 | pi05 (PaliGemma 2B + 300M expert) |
| 방법 | LoRA r=16, α=32, dropout 0.05, 타깃 `q/k/v/o_proj`·`up_proj` (소스에서 실재 확인) |
| GPU | **1× A100 80GB** (또는 H100 80GB) |
| 데이터 | G1_Dex3_ToastedBread 13.83 GB (HF에서 직접 스트리밍) |
| 예상 시간 | 12–36시간 |
| 예상 비용 | **$30–90** (A100 $1.3–2.5/hr 시장가 기준) |
| 산출물 | LoRA 어댑터(수백 MB) + 평가 로그 |

### 경로 B — 전체 파인튜닝
| 항목 | 값 |
|---|---|
| GPU | 8× H100 80GB |
| 시간 | 3–14일 |
| 비용 | **$6,000–40,000** |
| 비고 | 개인 규모에서는 비권장 |

### 경로 C — 시뮬레이터 선행 학습 (무료에 가까움)
| 항목 | 값 |
|---|---|
| 도구 | `unitree_rl_gym` / `unitree_rl_mjlab` / `unitree_sim_isaaclab` (전부 보유) |
| 실행 | IsaacLab은 RTX GPU 필요 → **이 기계 불가**, MuJoCo CPU는 가능(느림) |
| 용도 | 보행·균형 정책 선학습 → 실기 이전 검증 |

### 경로 D — 실기 제어 (로봇 필요)
`unitree_sdk2` + `unitree_sdk2_python` + `xr_teleoperate`(텔레오퍼레이션)로 실기 데이터 수집 →
경로 A로 학습 → 실기 배포. **로봇 하드웨어(G1/H1 등)가 있어야 시작됩니다.**

## 4. "유튜브로 로봇이 배운다"의 정확한 의미

| 단계 | 데이터 | 이 계획에서 |
|---|---|---|
| 눈(시각-언어) | 인터넷 이미지·텍스트·영상 + 자막 | VLM 사전학습 — **우리는 이미 학습된 PaliGemma를 사용** |
| 손(행동) | 로봇 시연 (관측+행동 쌍) | G1_Dex3 등 **HF 데이터셋으로 파인튜닝** |
| 몸(제어) | 관절 명령, 안전 한계 | SDK + 보행 정책 |

유튜브 영상은 1단계에만 기여합니다. 2단계에는 **행동 라벨**이 필요하고, 그건 로봇을
움직여 수집한 데이터(또는 텔레오퍼레이션)에서만 나옵니다.

## 5. 지금 이 기계에서 실제로 할 수 있는 것

1. ✅ 코드 코퍼스 질의 — `UNITREE-FILEBOX.db`에서 56개 저장소 전체 검색/검증
2. ✅ 파이프라인 검증 — pi05 설정·LoRA 타깃을 소스에서 재확인 (GPU 불요)
3. ✅ 데이터셋 스키마 검증 — 합성 LeRobot v3로 메타 검증
4. ✅ 데이터 소량 다운로드 — 예: R1_Dex1_Fold_Towel 1.34 GB는 디스크 여유 내
5. ❌ 실제 학습·실기 제어 — GPU와 로봇 필요

## 6. 대여 GPU 실행 절차 (경로 A, 재현용)

```bash
# 1) A100 80GB 인스턴스 (RunPod/Vast.ai/Lambda) + CUDA 12 이미지
# 2) 데이터 + 코드
huggingface-cli download unitreerobotics/G1_Dex3_ToastedBread_Dataset \
    --repo-type dataset --local-dir /data/toasted
git clone --depth 1 https://github.com/unitreerobotics/unitree_IL_lerobot
cd unitree_IL_lerobot && git submodule update --init --depth 1 unitree_lerobot/lerobot
# 3) LoRA 학습 (설정은 out/lora_recipe.json 과 동일)
python src/lerobot/scripts/lerobot_train.py \
  --dataset.repo_id=unitreerobotics/G1_Dex3_ToastedBread_Dataset \
  --policy.type=pi05 --policy.pretrained_path=lerobot/pi05_base \
  --policy.dtype=bfloat16 --policy.device=cuda \
  --policy.gradient_checkpointing=true \
  --output_dir=./outputs/pi05_lora --job_name=pi05_lora
# 4) 평가: 시뮬레이터 롤아웃 성공률 + (가능하면) 실기 전이
```

## 7. 한계 (정직)

1. 비용·시간은 **시장가 기반 추정**이며 이 기계에서 측정한 값이 아닙니다.
2. LoRA 레시피는 Gemma 관례 + 소스 확인으로 도출한 **우리 확장**입니다(원 저장소는 전체 파인튜닝만 안내).
3. 데이터셋 용량은 HF API `tree` 응답 실측이고, 학습 시 증강·프레임 샘플링에 따라 실제 사용량은 달라집니다.
4. 유튜브 영상 → 행동 라벨 자동 생성(예: 손 궤적 추출)은 **연구 주제**이며, 이 계획의 범위 밖입니다.
