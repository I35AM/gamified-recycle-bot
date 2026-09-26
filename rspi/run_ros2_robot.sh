#!/bin/bash
XAUTH=$HOME/.Xauthority

# preclean
sudo docker rm -f hanium_robot_space 2>/dev/null

echo "Launching Project ROS2 Humble Container with Arduino Support..."
docker run -it \
  --name=hanium_robot_space \
  --privileged \
  --net=host \
  --env="DISPLAY=$DISPLAY" \
  --volume="$XAUTH:/root/.Xauthority:rw" \
  --volume="/tmp/.X11-unix:/tmp/.X11-unix:rw" \
  --volume="/dev/bus/usb:/dev/bus/usb:rw" \
  --device=/dev/hailo0:/dev/hailo0 \
  --device=/dev/video0:/dev/video0 \
  --device=/dev/media0:/dev/media0 \
  --device=/dev/ttyACM0:/dev/ttyACM0 \
  hanium_robot_image:v1 \
  /bin/bash

# 💡 참고: 만약 라즈베리파이에 아두이노를 꽂았을 때 포트 이름이 ttyACM0이 아니라 ttyUSB0 이라면, 
# 위 코드에서 --device=/dev/ttyACM0:/dev/ttyACM0 부분을 --device=/dev/ttyUSB0:/dev/ttyUSB0 으로 변경해주세요!