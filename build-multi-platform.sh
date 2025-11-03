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
TAG=""
INSTALL_FFMPEG=${INSTALL_FFMPEG_ARG:-false}
PUSH=false
NO_CACHE=false
VERSION_FILE=".version"

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
    --no-version)
      NO_VERSION=true
      shift
      ;;
    --name)
      IMAGE_NAME="$2"
      shift 2
      ;;
    --ffmpeg)
      INSTALL_FFMPEG=true
      shift
      ;;
    --no-cache)
      NO_CACHE=true
      shift
      ;;
    --help)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Build multi-platform Docker image for ARM64 and AMD64"
      echo ""
      echo "Options:"
      echo "  --push          Push image to registry after building"
      echo "  --tag TAG       Image tag (default: auto-generated date-based version)"
      echo "  --no-version    Don't generate version tag, only use specified tag or latest"
      echo "  --name NAME     Image name (default: mrwetsnow/openai-edge-tts)"
      echo "  --ffmpeg        Include FFmpeg in the build"
      echo "  --no-cache      Build without using cache"
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

# Generate version tag automatically unless disabled
VERSION_TAG=""
if [ "${NO_VERSION:-false}" != "true" ]; then
    # Get today's date in YYYYMMDD format
    TODAY=$(date +%Y%m%d)

    # Read or initialize version file
    if [ -f "$VERSION_FILE" ]; then
        LAST_DATE=$(head -n1 "$VERSION_FILE" | awk '{print $1}')
        LAST_BUILD=$(head -n1 "$VERSION_FILE" | awk '{print $2}')
        LAST_PATCH=$(head -n1 "$VERSION_FILE" | awk '{print $3}')

        # Default values if missing
        LAST_BUILD=${LAST_BUILD:-00}
        LAST_PATCH=${LAST_PATCH:-00}
    else
        LAST_DATE=""
        LAST_BUILD="00"
        LAST_PATCH="00"
    fi

    # Increment version based on date
    if [ "$LAST_DATE" = "$TODAY" ]; then
        # Same day - increment patch number
        NEW_PATCH=$((10#$LAST_PATCH + 1))
        # If patch reaches 100, increment build and reset patch
        if [ $NEW_PATCH -ge 100 ]; then
            NEW_BUILD=$((10#$LAST_BUILD + 1))
            NEW_PATCH=0
        else
            NEW_BUILD=$LAST_BUILD
        fi
    else
        # New day - reset patch to 0, increment build
        NEW_BUILD=$((10#$LAST_BUILD + 1))
        NEW_PATCH=0
    fi

    # Format with leading zeros (two digits each)
    FORMATTED_BUILD=$(printf "%02d" $NEW_BUILD)
    FORMATTED_PATCH=$(printf "%02d" $NEW_PATCH)

    # Create version tag: YYYYMMDD.BUILD.PATCH
    VERSION_TAG="${TODAY}.${FORMATTED_BUILD}.${FORMATTED_PATCH}"

    # Save version to file
    echo "${TODAY} ${FORMATTED_BUILD} ${FORMATTED_PATCH}" > "$VERSION_FILE"

    echo -e "${BLUE}Auto-generated version: ${GREEN}${VERSION_TAG}${NC}"
fi

# Set default tag to "latest" if not provided
if [ -z "$TAG" ]; then
    TAG="latest"
fi

# Always tag as latest, plus version tag if generated
TAGS_TO_BUILD="-t ${IMAGE_NAME}:${TAG}"
if [ -n "$VERSION_TAG" ]; then
    TAGS_TO_BUILD="${TAGS_TO_BUILD} -t ${IMAGE_NAME}:${VERSION_TAG}"
fi

echo -e "${BLUE}Building multi-platform Docker image${NC}"
echo -e "  Image: ${GREEN}${IMAGE_NAME}${NC}"
echo -e "  Tags: ${GREEN}${TAG}${NC}" $(if [ -n "$VERSION_TAG" ]; then echo -e "+ ${GREEN}${VERSION_TAG}${NC}"; fi)
echo -e "  Platforms: ${GREEN}linux/amd64,linux/arm64${NC}"
echo -e "  FFmpeg: ${GREEN}${INSTALL_FFMPEG}${NC}"
echo -e "  Push: ${GREEN}${PUSH}${NC}"
echo -e "  No Cache: ${GREEN}${NO_CACHE}${NC}"
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
BUILD_ARGS="${BUILD_ARGS} ${TAGS_TO_BUILD}"

# Add tag suffix for FFmpeg builds
if [ "$INSTALL_FFMPEG" = true ]; then
    FFMPEG_TAG="${TAG}-ffmpeg"
    BUILD_ARGS="${BUILD_ARGS} -t ${IMAGE_NAME}:${FFMPEG_TAG}"
    if [ -n "$VERSION_TAG" ]; then
        FFMPEG_VERSION_TAG="${VERSION_TAG}-ffmpeg"
        BUILD_ARGS="${BUILD_ARGS} -t ${IMAGE_NAME}:${FFMPEG_VERSION_TAG}"
    fi
fi

# Multi-platform builds require --push; cannot use --load
if [ "$PUSH" = false ]; then
    echo -e "${YELLOW}Note: Multi-platform builds require --push to registry.${NC}"
    echo -e "${YELLOW}Building for current platform only for local testing...${NC}"
    echo ""

    # Build for current platform only
    CURRENT_PLATFORM=$(docker buildx inspect --bootstrap | grep "Platforms:" | awk '{print $2}')
    echo -e "${BLUE}Building for platform: ${CURRENT_PLATFORM}${NC}"
    BUILD_CMD="docker build"
    if [ "$NO_CACHE" = true ]; then
        BUILD_CMD="${BUILD_CMD} --no-cache"
    fi

    # Build with all tags
    TAG_CMDS="-t ${IMAGE_NAME}:${TAG}"
    if [ -n "$VERSION_TAG" ]; then
        TAG_CMDS="${TAG_CMDS} -t ${IMAGE_NAME}:${VERSION_TAG}"
    fi

    ${BUILD_CMD} ${TAG_CMDS} \
                 --build-arg INSTALL_FFMPEG=${INSTALL_FFMPEG} \
                 .

    if [ "$INSTALL_FFMPEG" = true ]; then
        docker tag "${IMAGE_NAME}:${TAG}" "${IMAGE_NAME}:${FFMPEG_TAG}"
        if [ -n "$VERSION_TAG" ]; then
            docker tag "${IMAGE_NAME}:${VERSION_TAG}" "${IMAGE_NAME}:${FFMPEG_VERSION_TAG}"
        fi
    fi

    echo -e "${GREEN}✓ Build complete!${NC}"
    echo ""
    echo "For multi-platform builds, use --push flag:"
    echo "  ./build-multi-platform.sh --ffmpeg --push"
    exit 0
fi

BUILD_ARGS="${BUILD_ARGS} --push"

if [ "$NO_CACHE" = true ]; then
    BUILD_ARGS="${BUILD_ARGS} --no-cache"
fi

echo -e "${BLUE}Starting multi-platform build...${NC}"
docker buildx build ${BUILD_ARGS} .

echo -e "${GREEN}✓ Build and push complete!${NC}"
echo ""
echo "Verify multi-platform support with:"
echo "  docker buildx imagetools inspect ${IMAGE_NAME}:${TAG}"
if [ -n "$VERSION_TAG" ]; then
    echo "  docker buildx imagetools inspect ${IMAGE_NAME}:${VERSION_TAG}"
fi
if [ "$INSTALL_FFMPEG" = true ]; then
    echo "  docker buildx imagetools inspect ${IMAGE_NAME}:${FFMPEG_TAG}"
    if [ -n "$VERSION_TAG" ]; then
        echo "  docker buildx imagetools inspect ${IMAGE_NAME}:${FFMPEG_VERSION_TAG}"
    fi
fi

