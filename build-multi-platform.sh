#!/bin/bash
# Build script for multi-platform Docker images

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
IMAGE_NAME=${IMAGE_NAME:-"mrwetsnow/openai-edge-tts"}
TAG=${TAG:-"latest"}
INSTALL_FFMPEG=${INSTALL_FFMPEG_ARG:-false}
PUSH=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --push)
      PUSH=true
      shift
      ;;
    --tag)
      TAG="$2"
      shift 2
      ;;
    --name)
      IMAGE_NAME="$2"
      shift 2
      ;;
    --ffmpeg)
      INSTALL_FFMPEG=true
      shift
      ;;
    --help)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Build multi-platform Docker image for ARM64 and AMD64"
      echo ""
      echo "Options:"
      echo "  --push          Push image to registry after building"
      echo "  --tag TAG       Image tag (default: latest)"
      echo "  --name NAME     Image name (default: mrwetsnow/openai-edge-tts)"
      echo "  --ffmpeg        Include FFmpeg in the build"
      echo "  --help          Show this help message"
      echo ""
      echo "Examples:"
      echo "  $0                                    # Build local only"
      echo "  $0 --push                            # Build and push"
      echo "  $0 --push --tag v2.0.0               # Build, tag, and push"
      echo "  $0 --push --ffmpeg                   # Build with FFmpeg and push"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done

echo -e "${BLUE}Building multi-platform Docker image${NC}"
echo -e "  Image: ${GREEN}${IMAGE_NAME}:${TAG}${NC}"
echo -e "  Platforms: ${GREEN}linux/amd64,linux/arm64${NC}"
echo -e "  FFmpeg: ${GREEN}${INSTALL_FFMPEG}${NC}"
echo -e "  Push: ${GREEN}${PUSH}${NC}"
echo ""

# Check if buildx is available
if ! docker buildx version > /dev/null 2>&1; then
    echo -e "${YELLOW}Warning: Docker buildx not found. Install it for multi-platform builds.${NC}"
    echo "Attempting to build for current platform only..."
    docker build -t "${IMAGE_NAME}:${TAG}" \
                 --build-arg INSTALL_FFMPEG=${INSTALL_FFMPEG} \
                 .
    exit 0
fi

# Create and use builder
BUILDER_NAME="multiarch-builder"
if ! docker buildx ls | grep -q "${BUILDER_NAME}"; then
    echo -e "${BLUE}Creating buildx builder: ${BUILDER_NAME}${NC}"
    docker buildx create --name "${BUILDER_NAME}" --driver docker-container --use --bootstrap
else
    echo -e "${BLUE}Using existing buildx builder: ${BUILDER_NAME}${NC}"
    docker buildx use "${BUILDER_NAME}"
fi

# Build args
BUILD_ARGS="--platform linux/amd64,linux/arm64"
BUILD_ARGS="${BUILD_ARGS} --build-arg INSTALL_FFMPEG=${INSTALL_FFMPEG}"
BUILD_ARGS="${BUILD_ARGS} -t ${IMAGE_NAME}:${TAG}"

# Add tag suffix for FFmpeg builds
if [ "$INSTALL_FFMPEG" = true ]; then
    FFMPEG_TAG="${TAG}-ffmpeg"
    BUILD_ARGS="${BUILD_ARGS} -t ${IMAGE_NAME}:${FFMPEG_TAG}"
fi

# Multi-platform builds require --push; cannot use --load
if [ "$PUSH" = false ]; then
    echo -e "${YELLOW}Note: Multi-platform builds require --push to registry.${NC}"
    echo -e "${YELLOW}Building for current platform only for local testing...${NC}"
    echo ""

    # Build for current platform only
    CURRENT_PLATFORM=$(docker buildx inspect --bootstrap | grep "Platforms:" | awk '{print $2}')
    echo -e "${BLUE}Building for platform: ${CURRENT_PLATFORM}${NC}"
    docker build -t "${IMAGE_NAME}:${TAG}" \
                 --build-arg INSTALL_FFMPEG=${INSTALL_FFMPEG} \
                 .

    if [ "$INSTALL_FFMPEG" = true ]; then
        docker tag "${IMAGE_NAME}:${TAG}" "${IMAGE_NAME}:${FFMPEG_TAG}"
    fi

    echo -e "${GREEN}✓ Build complete!${NC}"
    echo ""
    echo "For multi-platform builds, use --push flag:"
    echo "  ./build-multi-platform.sh --ffmpeg --push"
    exit 0
fi

BUILD_ARGS="${BUILD_ARGS} --push"

echo -e "${BLUE}Starting multi-platform build...${NC}"
docker buildx build ${BUILD_ARGS} .

echo -e "${GREEN}✓ Build and push complete!${NC}"
echo ""
echo "Verify multi-platform support with:"
echo "  docker buildx imagetools inspect ${IMAGE_NAME}:${TAG}"
if [ "$INSTALL_FFMPEG" = true ]; then
    echo "  docker buildx imagetools inspect ${IMAGE_NAME}:${FFMPEG_TAG}"
fi

