import cv2
import torch
import numpy as np
from model.RIFE import Model # RIFE 공식 저장소의 모델 클래스

# 1. 모델 초기화 및 가중치 로드
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model = Model()
model.load_model('train_log', -1) # 다운로드한 가중치 폴더 경로 지정
model.eval()
model.device()

# 2. 프레임 전처리 함수
def preprocess(img):
    # HWC (OpenCV) -> CHW (PyTorch 구조) 및 0~1 정규화
    img = (torch.tensor(img.transpose(2, 0, 1)).to(device) / 255.0).unsqueeze(0)
    return img

# 3. 테스트용 프레임 읽기
# (실제 환경에서는 DXGI 등으로 캡처한 프레임 버퍼를 바로 넘겨받습니다)
frame0 = cv2.imread('frame_0.png')
frame1 = cv2.imread('frame_1.png')

# 패딩 처리 (RIFE 네트워크는 32의 배수 해상도를 요구합니다)
h, w, _ = frame0.shape
ph = ((h - 1) // 32 + 1) * 32
pw = ((w - 1) // 32 + 1) * 32
padding = (0, pw - w, 0, ph - h)

img0 = torch.nn.functional.pad(preprocess(frame0), padding)
img1 = torch.nn.functional.pad(preprocess(frame1), padding)

# 4. 중간 프레임 추론 (보간)
with torch.no_grad():
    # timestep=0.5는 두 프레임의 정확히 중간(0.5) 시점을 의미합니다.
    mid_img = model.inference(img0, img1, timestep=0.5)

# 5. 후처리 및 결과 출력
mid_img = mid_img[0].cpu().numpy().transpose(1, 2, 0) # CHW -> HWC 원복
mid_img = mid_img[:h, :w] # 추가했던 패딩 제거
mid_img = (mid_img * 255.0).round().astype(np.uint8)

cv2.imshow('Interpolated Frame', mid_img)
cv2.waitKey(0)