# Third-party code. Each is pinned to a commit and to the SHA-256 of that commit's
# archive, and fetched into the build tree. Archives are shared between presets
# through build/_downloads, so that a new preset does not download them again.

include(FetchContent)

set(ITCH_DOWNLOADS "${PROJECT_SOURCE_DIR}/build/_downloads")

FetchContent_Declare(googletest
  URL https://github.com/google/googletest/archive/063de7e9578f82b369302001269680b4b1553359.tar.gz
  URL_HASH SHA256=623fe05d283020f37a579a8985ba338091ac7229da62b5591ff40acdf05b95e2
  DOWNLOAD_DIR "${ITCH_DOWNLOADS}"
  DOWNLOAD_NAME googletest-v1.18.0.tar.gz
  SYSTEM)

FetchContent_Declare(benchmark
  URL https://github.com/google/benchmark/archive/a6b4a9f00b36c19af9997d68a1cf6e0b5b3d032c.tar.gz
  URL_HASH SHA256=1e34f52437882bfe010f955580e77a19b4fb0a702ef678609b509a834296075c
  DOWNLOAD_DIR "${ITCH_DOWNLOADS}"
  DOWNLOAD_NAME benchmark-v1.10.0.tar.gz
  SYSTEM)

FetchContent_Declare(hdr_histogram
  URL https://github.com/HdrHistogram/HdrHistogram_c/archive/30e9ce6ef5dcdc60f9270e1ca03c0966eaaf71d0.tar.gz
  URL_HASH SHA256=7f882d20defe473d3e42e8424805db6b37cbec1d044082f76ca1ab32726dbc8d
  DOWNLOAD_DIR "${ITCH_DOWNLOADS}"
  DOWNLOAD_NAME HdrHistogram_c-0.12.1.tar.gz
  SYSTEM)

# GoogleTest, as GTest::gtest and GTest::gtest_main. scripts/fault_matrix.py sets
# ITCH_PREBUILT_DEPS to a preset's build/<preset> directory, so that its many scratch
# builds link one copy of the same pinned library instead of compiling it again.
function(itch_require_gtest)
  if(TARGET GTest::gtest)
    return()
  endif()
  if(ITCH_PREBUILT_DEPS)
    foreach(lib gtest gtest_main)
      add_library(GTest::${lib} STATIC IMPORTED GLOBAL)
      set_target_properties(GTest::${lib} PROPERTIES
        IMPORTED_LOCATION "${ITCH_PREBUILT_DEPS}/lib/lib${lib}.a"
        INTERFACE_SYSTEM_INCLUDE_DIRECTORIES "${ITCH_PREBUILT_DEPS}/_deps/googletest-src/googletest/include"
        INTERFACE_INCLUDE_DIRECTORIES "${ITCH_PREBUILT_DEPS}/_deps/googletest-src/googletest/include")
    endforeach()
    set_target_properties(GTest::gtest_main PROPERTIES INTERFACE_LINK_LIBRARIES GTest::gtest)
    return()
  endif()
  set(BUILD_GMOCK OFF CACHE BOOL "" FORCE)
  set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
  FetchContent_MakeAvailable(googletest)
endfunction()

# Google Benchmark as benchmark::benchmark and HdrHistogram_c as
# hdr_histogram::hdr_histogram_static. For the benchmarks: call this from the
# subdirectory that uses them. With ITCH_PREBUILT_DEPS a scratch tree imports the two
# libraries instead of configuring them, and nothing links them unless it asks.
function(itch_require_bench_deps)
  if(ITCH_PREBUILT_DEPS)
    add_library(benchmark::benchmark STATIC IMPORTED GLOBAL)
    set_target_properties(benchmark::benchmark PROPERTIES
      IMPORTED_LOCATION "${ITCH_PREBUILT_DEPS}/_deps/benchmark-build/src/libbenchmark.a"
      INTERFACE_SYSTEM_INCLUDE_DIRECTORIES "${ITCH_PREBUILT_DEPS}/_deps/benchmark-src/include"
      INTERFACE_INCLUDE_DIRECTORIES "${ITCH_PREBUILT_DEPS}/_deps/benchmark-src/include")
    add_library(hdr_histogram::hdr_histogram_static STATIC IMPORTED GLOBAL)
    set_target_properties(hdr_histogram::hdr_histogram_static PROPERTIES
      IMPORTED_LOCATION "${ITCH_PREBUILT_DEPS}/_deps/hdr_histogram-build/src/libhdr_histogram_static.a"
      INTERFACE_SYSTEM_INCLUDE_DIRECTORIES "${ITCH_PREBUILT_DEPS}/_deps/hdr_histogram-src/include"
      INTERFACE_INCLUDE_DIRECTORIES "${ITCH_PREBUILT_DEPS}/_deps/hdr_histogram-src/include")
    return()
  endif()
  enable_language(C)
  set(BENCHMARK_ENABLE_TESTING OFF CACHE BOOL "" FORCE)
  set(BENCHMARK_ENABLE_INSTALL OFF CACHE BOOL "" FORCE)
  set(BENCHMARK_ENABLE_WERROR OFF CACHE BOOL "" FORCE)
  set(HDR_HISTOGRAM_BUILD_PROGRAMS OFF CACHE BOOL "" FORCE)
  set(HDR_HISTOGRAM_BUILD_SHARED OFF CACHE BOOL "" FORCE)
  set(HDR_LOG_REQUIRED OFF CACHE BOOL "" FORCE)
  FetchContent_MakeAvailable(benchmark hdr_histogram)
endfunction()
