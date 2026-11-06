cmake_minimum_required(VERSION 3.28)

project(itch_orderbook VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(CMAKE_CXX_SCAN_FOR_MODULES OFF)

option(ITCH_BUILD_TESTS "Build the ctest suite" ON)
option(ITCH_BUILD_TOOLS "Build the command-line tools" ON)
option(ITCH_BUILD_FUZZ "Build the fuzz targets" ON)
option(ITCH_BUILD_BENCH "Build the benchmarks" ON)
option(ITCH_FUZZ "Link the fuzz targets with libFuzzer instead of the corpus replay runner" OFF)
option(ITCH_TIDY "Run clang-tidy on our targets while compiling" OFF)
option(ITCH_COMPARISON_QUEUES "Fetch the third-party queues T-SPSC-1 compares against" OFF)
set(ITCH_SANITIZE "" CACHE STRING "Sanitizers, comma separated, for example address,undefined")
# A negative control for the SPSC ring's tests and spsc_stress, never for a release:
# tests/support/spsc_ordering.hpp weakens the named order to relaxed in those builds.
set(ITCH_SPSC_NEGATIVE_CONTROL "" CACHE STRING
  "Weaken one SPSC order in the ring's tests: publish, observe, return or reclaim. Empty for none")
set(ITCH_LIBURING_ROOT "" CACHE PATH "Prefix of a liburing build, Linux only. Empty leaves it off")

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")
include(ItchOptions)
include(ItchTidy)
include(ItchDependencies)
include(ItchCacheLine)
include(ItchKernels)

enable_testing()

itch_require_liburing()
if(ITCH_COMPARISON_QUEUES)
  itch_require_comparison_queues()
endif()

add_subdirectory(src)
if(ITCH_BUILD_TOOLS)
  add_subdirectory(tools)
endif()
if(ITCH_BUILD_TESTS)
  add_subdirectory(tests)
endif()
if(ITCH_BUILD_FUZZ)
  add_subdirectory(fuzz)
endif()
if(ITCH_BUILD_BENCH)
  add_subdirectory(bench)
endif()
if(EMSCRIPTEN)
  add_subdirectory(bench/wasm)
endif()
