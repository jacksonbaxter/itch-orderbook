# Which parsing kernels this build has (include/itch/kernels/kernels.hpp). The choice is made
# here and not by the preprocessor: one directory of include/itch/kernels/arch/ goes on
# itch_parser's include path, and its kernels_built.hpp names the kernels and how each runs.
#
#   arm64    scalar, neon
#   x86_64   scalar, ssse3, avx2, avx512vbmi, each vector kernel under its own target
#            attribute, so the build needs no -m flag and runs on any x86-64
#   wasm     scalar, wasm, with -msimd128 on everything that links the parser
#   generic  scalar
#
# ITCH_KERNEL_ARCH and ITCH_KERNELS are set for tests/CMakeLists.txt, which runs each
# kernel's tests as its own ctest entry.

if(EMSCRIPTEN)
  set(itch_kernel_target "wasm")
elseif(APPLE AND CMAKE_OSX_ARCHITECTURES)
  list(LENGTH CMAKE_OSX_ARCHITECTURES itch_osx_arch_count)
  if(NOT itch_osx_arch_count EQUAL 1)
    message(FATAL_ERROR "the kernels need one architecture a build, not ${CMAKE_OSX_ARCHITECTURES}")
  endif()
  set(itch_kernel_target "${CMAKE_OSX_ARCHITECTURES}")
else()
  set(itch_kernel_target "${CMAKE_SYSTEM_PROCESSOR}")
endif()

if(itch_kernel_target STREQUAL "wasm")
  set(ITCH_KERNEL_ARCH "wasm")
  set(ITCH_KERNELS scalar wasm)
elseif(itch_kernel_target MATCHES "^(arm64|aarch64|ARM64)$")
  set(ITCH_KERNEL_ARCH "arm64")
  set(ITCH_KERNELS scalar neon)
elseif(itch_kernel_target MATCHES "^(x86_64|AMD64|amd64)$")
  set(ITCH_KERNEL_ARCH "x86_64")
  set(ITCH_KERNELS scalar ssse3 avx2 avx512vbmi)
else()
  set(ITCH_KERNEL_ARCH "generic")
  set(ITCH_KERNELS scalar)
endif()
message(STATUS "Parsing kernels: ${ITCH_KERNEL_ARCH} (${ITCH_KERNELS})")

# The arch directory and, for WebAssembly, the flag its kernel needs.
function(itch_add_kernels target)
  target_include_directories(${target} PUBLIC
    "${PROJECT_SOURCE_DIR}/include/itch/kernels/arch/${ITCH_KERNEL_ARCH}")
  if(ITCH_KERNEL_ARCH STREQUAL "wasm")
    target_compile_options(${target} PUBLIC -msimd128)
  endif()
endfunction()
