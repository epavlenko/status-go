#!/usr/bin/env bash
# Source this file to set up environment variables for running tests with gotestsum.
# Usage: source ./scripts/set-test-env.sh

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "This script must be sourced, not executed."
  echo "Usage: source ./scripts/set-test-env.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -n "${LIBSTORAGE_PATH:-}" ]]; then
  export LIBS_DIR="${LIBS_DIR:-$LIBSTORAGE_PATH/lib}"
else
  export LIBS_DIR="${LIBS_DIR:-$(realpath "$SCRIPT_DIR/../libs")}"
fi

if [[ -n "${LIBSDS_PATH:-}" ]]; then
  export NIM_SDS_LIB_DIR="${NIM_SDS_LIB_DIR:-$LIBSDS_PATH/lib}"
  export NIM_SDS_INC_DIR="${NIM_SDS_INC_DIR:-$LIBSDS_PATH/include}"
else
  export NIM_SDS_LIB_DIR="${NIM_SDS_LIB_DIR:-$(realpath "$SCRIPT_DIR/../../nim-sds/build")}"
  export NIM_SDS_INC_DIR="${NIM_SDS_INC_DIR:-$(realpath "$SCRIPT_DIR/../../nim-sds/library")}"
fi

export NWAKU_SOURCE_DIR="${NWAKU_SOURCE_DIR:-$(realpath "$SCRIPT_DIR/../../nwaku")}"

cgo_cflags="-I$NIM_SDS_INC_DIR"
cgo_ldflags="-L$NIM_SDS_LIB_DIR -lsds"
runtime_lib_dirs="$NIM_SDS_LIB_DIR"

if [[ "${USE_NWAKU:-false}" == "true" ]]; then
  cgo_cflags="-I$NWAKU_SOURCE_DIR/library $cgo_cflags"
  cgo_ldflags="-L$NWAKU_SOURCE_DIR/build -lwaku -Wl,-rpath,$NWAKU_SOURCE_DIR/build $cgo_ldflags"
  runtime_lib_dirs="$NWAKU_SOURCE_DIR/build:$runtime_lib_dirs"
fi

if [[ "${USE_LOGOS_STORAGE:-true}" == "true" ]]; then
  cgo_cflags="-I$LIBS_DIR $cgo_cflags"
  cgo_ldflags="-L$LIBS_DIR -lstorage -Wl,-rpath,$LIBS_DIR $cgo_ldflags"
  runtime_lib_dirs="$LIBS_DIR:$runtime_lib_dirs"
fi

export CGO_CFLAGS="$cgo_cflags"
export CGO_LDFLAGS="$cgo_ldflags"

if [[ "$OSTYPE" == "darwin"* ]]; then
  export DYLD_LIBRARY_PATH="$runtime_lib_dirs:${DYLD_LIBRARY_PATH:-}"
  echo "Using test environment (macOS)"
else
  export LD_LIBRARY_PATH="$runtime_lib_dirs:${LD_LIBRARY_PATH:-}"
  echo "Using test environment (Linux)"
fi

if [[ "${USE_LOGOS_STORAGE:-true}" == "true" ]] && [[ ! -f "$LIBS_DIR/libstorage.so" && ! -f "$LIBS_DIR/libstorage.dylib" && ! -f "$LIBS_DIR/libstorage.dll" ]]; then
  echo "Warning: libstorage shared library not found in $LIBS_DIR"
fi
if [[ ! -f "$NIM_SDS_LIB_DIR/libsds.so" && ! -f "$NIM_SDS_LIB_DIR/libsds.dylib" && ! -f "$NIM_SDS_LIB_DIR/libsds.dll" ]]; then
  echo "Warning: libsds shared library not found in $NIM_SDS_LIB_DIR"
fi

echo "Test environment variables set:"
echo "  LIBS_DIR=$LIBS_DIR"
echo "  NIM_SDS_LIB_DIR=$NIM_SDS_LIB_DIR"
echo "  NIM_SDS_INC_DIR=$NIM_SDS_INC_DIR"
echo "  NWAKU_SOURCE_DIR=$NWAKU_SOURCE_DIR"
echo "  CGO_CFLAGS=$CGO_CFLAGS"
echo "  CGO_LDFLAGS=$CGO_LDFLAGS"
echo ""
echo "You can now run tests with gotestsum, for example:"
echo '  gotestsum --packages="./services/logosstorage" -f testname -- -count 1 -tags "gowaku_no_rln gowaku_skip_migrations"'
