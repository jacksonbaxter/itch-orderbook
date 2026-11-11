# Third-party code. Each is pinned to a commit and to the SHA-256 of that commit's
# archive, and fetched into the build tree. Archives are shared between presets
# through build/_downloads, so that a new preset does not download them again.

include(FetchContent)

set(ITCH_DOWNLOADS "${PROJECT_SOURCE_DIR}/build/_downloads")
set(ITCH_LIBURING_VERSION 2.15)

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

# The queues bench_spsc compares the project's ring against, T-SPSC-1 among them. Off by
# default, so that no default build downloads them. Each is data: SOURCE_SUBDIR names a
# directory none of them has, so that no build script of theirs is ever run, and only
# headers are used. Boost publishes its release tree, submodules included, only as a
# release asset, so its URL names the tag and not the commit. boost-1.92.0 is
# afdfa32505af73e3d208144b3f623f0096cb62b6. folly is its release v2026.10.26.00, of which
# ProducerConsumerQueue.h and the headers it includes are used and nothing is built: they
# need no configure result, so folly-config.h is written empty (NOTES.md, "The SPSC ring").
# atomic_queue is the commit of the results LC-1 cites, and xenium the commit LC-9 read.
if(ITCH_COMPARISON_QUEUES)
  FetchContent_Declare(boost_headers
    URL https://github.com/boostorg/boost/releases/download/boost-1.92.0/boost-1.92.0-b2-nodocs.tar.xz
    URL_HASH SHA256=ea7b982002cc9dfbe59b0b217b206f470dc75f3de0bb2973d844118934d82411
    DOWNLOAD_DIR "${ITCH_DOWNLOADS}"
    DOWNLOAD_NAME boost-1.92.0-b2-nodocs.tar.xz
    SOURCE_SUBDIR itch-headers-only
    SYSTEM)

  FetchContent_Declare(rigtorp_spsc
    URL https://github.com/rigtorp/SPSCQueue/archive/565a5149d54930463d58cb0f69b978d439555e66.tar.gz
    URL_HASH SHA256=23d57eddf74dcd99d206b9a4b663ea6644d7a2ad8c0663ee2131d5bc5b952e1e
    DOWNLOAD_DIR "${ITCH_DOWNLOADS}"
    DOWNLOAD_NAME SPSCQueue-v1.1.tar.gz
    SOURCE_SUBDIR itch-headers-only
    SYSTEM)

  FetchContent_Declare(moodycamel_rwq
    URL https://github.com/cameron314/readerwriterqueue/archive/8b2176698e9bdaba653cdc20c32b54737a934b47.tar.gz
    URL_HASH SHA256=4cdffdcab099583c2080dc36ecded701f16e4512674b9baef0f0cc5d1b9964dd
    DOWNLOAD_DIR "${ITCH_DOWNLOADS}"
    DOWNLOAD_NAME readerwriterqueue-v1.0.7.tar.gz
    SOURCE_SUBDIR itch-headers-only
    SYSTEM)

  FetchContent_Declare(folly
    URL https://github.com/facebook/folly/archive/c8c7adb72c6d73e2fced5ff5cebc96aa3ff65b2c.tar.gz
    URL_HASH SHA256=2a2d95f2f220264b283b34dd0ec73fa3f4f3ebd8af663407277087d6f1df8d5d
    DOWNLOAD_DIR "${ITCH_DOWNLOADS}"
    DOWNLOAD_NAME folly-c8c7adb7.tar.gz
    SOURCE_SUBDIR itch-headers-only
    SYSTEM)

  FetchContent_Declare(atomic_queue
    URL https://github.com/max0x7ba/atomic_queue/archive/b7f1befe89446752767243c5e5550779a9a5eed2.tar.gz
    URL_HASH SHA256=e8aad8b233fbb8f7a4f5eae8f28dca8f1f11b7100e2e2be10a49b26259c32a31
    DOWNLOAD_DIR "${ITCH_DOWNLOADS}"
    DOWNLOAD_NAME atomic_queue-b7f1befe.tar.gz
    SOURCE_SUBDIR itch-headers-only
    SYSTEM)

  FetchContent_Declare(xenium
    URL https://github.com/mpoeter/xenium/archive/1c449ae953ce2a440b0d16c5ed1181d2754860ab.tar.gz
    URL_HASH SHA256=3057321e33c30be3a2ff5d0691cc70cdda8da046d73e5b822ccee4c9d12c83d4
    DOWNLOAD_DIR "${ITCH_DOWNLOADS}"
    DOWNLOAD_NAME xenium-1c449ae9.tar.gz
    SOURCE_SUBDIR itch-headers-only
    SYSTEM)
endif()

# mimalloc, for the node book's mimalloc arm (T-ARENA-1): release v3.5.3, its commit
# d4881d338125e1cb7c47ba4cfb398d6f7c0c8d45. Off unless ITCH_MIMALLOC is on; the release and
# tidy presets turn it on.
if(ITCH_MIMALLOC)
  FetchContent_Declare(mimalloc
    URL https://github.com/microsoft/mimalloc/archive/d4881d338125e1cb7c47ba4cfb398d6f7c0c8d45.tar.gz
    URL_HASH SHA256=43857a9e4f26412e970cbe49635d0465c2de6e77a697ce3d4b926a473d1045ca
    DOWNLOAD_DIR "${ITCH_DOWNLOADS}"
    DOWNLOAD_NAME mimalloc-v3.5.3.tar.gz
    SYSTEM)
endif()

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

# The comparison queues as itch::comparison_queues, plus one object file under the build
# tree that instantiates each, so that a header which stops compiling shows up here and
# not only in bench_spsc. That object is compiled and never linked or run.
function(itch_require_comparison_queues)
  FetchContent_MakeAvailable(boost_headers rigtorp_spsc moodycamel_rwq folly atomic_queue xenium)
  set(folly_config "${CMAKE_BINARY_DIR}/itch_folly_config")
  file(WRITE "${folly_config}/folly/folly-config.h"
    "// Written by cmake/ItchDependencies.cmake: empty, since ProducerConsumerQueue.h and the\n"
    "// folly headers it includes read no configure result.\n"
    "#pragma once\n")
  add_library(itch_comparison_queues INTERFACE)
  add_library(itch::comparison_queues ALIAS itch_comparison_queues)
  target_include_directories(itch_comparison_queues SYSTEM INTERFACE
    "${boost_headers_SOURCE_DIR}"
    "${rigtorp_spsc_SOURCE_DIR}/include"
    "${moodycamel_rwq_SOURCE_DIR}"
    "${folly_config}"
    "${folly_SOURCE_DIR}"
    "${atomic_queue_SOURCE_DIR}/include"
    "${xenium_SOURCE_DIR}")
  target_compile_definitions(itch_comparison_queues INTERFACE ITCH_HAVE_COMPARISON_QUEUES)
  # xenium's vyukov_bounded_queue holds its slots in std::aligned_storage, which C++23
  # deprecates, and the warning is raised where our code instantiates it.
  target_compile_options(itch_comparison_queues INTERFACE -Wno-deprecated-declarations)

  set(check "${CMAKE_BINARY_DIR}/itch_comparison_queues_check.cpp")
  file(WRITE "${check}" [=[
#include <cstdint>
#include <memory>

#include <atomic_queue/atomic_queue.h>
#include <boost/lockfree/spsc_queue.hpp>
#include <folly/ProducerConsumerQueue.h>
#include <readerwriterqueue.h>
#include <rigtorp/SPSCQueue.h>
#include <xenium/vyukov_bounded_queue.hpp>

std::uint32_t itch_comparison_queues_check();

std::uint32_t itch_comparison_queues_check() {
  boost::lockfree::spsc_queue<std::uint32_t, boost::lockfree::capacity<16>> boost_queue;
  rigtorp::SPSCQueue<std::uint32_t> rigtorp_queue{16};
  moodycamel::ReaderWriterQueue<std::uint32_t> moodycamel_queue{16};
  folly::ProducerConsumerQueue<std::uint32_t> folly_queue{16};
  atomic_queue::AtomicQueueB2<std::uint32_t, std::allocator<std::uint32_t>, true, false, true>
      atomic_queue_queue{16};
  xenium::vyukov_bounded_queue<std::uint32_t> xenium_queue{16};

  std::uint32_t total = 0;
  std::uint32_t value = 0;
  if (boost_queue.push(1U) && boost_queue.pop(value)) {
    total += value;
  }
  if (rigtorp_queue.try_push(2U)) {
    total += *rigtorp_queue.front();
    rigtorp_queue.pop();
  }
  if (moodycamel_queue.try_enqueue(3U) && moodycamel_queue.try_dequeue(value)) {
    total += value;
  }
  if (folly_queue.write(4U) && folly_queue.read(value)) {
    total += value;
  }
  if (atomic_queue_queue.try_push(5U) && atomic_queue_queue.try_pop(value)) {
    total += value;
  }
  if (xenium_queue.try_push(6U) && xenium_queue.try_pop(value)) {
    total += value;
  }
  return total;
}
]=])
  add_library(itch_comparison_queues_check OBJECT "${check}")
  target_link_libraries(itch_comparison_queues_check PRIVATE itch::comparison_queues)
  itch_apply_options(itch_comparison_queues_check)
endfunction()

# liburing, on Linux only, from a build the caller names at configure time: on debian,
# -DITCH_LIBURING_ROOT=<the liburing prefix in its toolchain>. Empty leaves it off, which
# is what macOS and every default build do. The ring modes U1 to U5 link it, through
# itch_udp_modes().
function(itch_require_liburing)
  if(NOT ITCH_LIBURING_ROOT)
    message(STATUS "liburing: off")
    return()
  endif()
  if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR "ITCH_LIBURING_ROOT is set, but liburing is Linux only")
  endif()
  set(version_header "${ITCH_LIBURING_ROOT}/include/liburing/io_uring_version.h")
  set(library "${ITCH_LIBURING_ROOT}/lib/liburing.a")
  if(NOT EXISTS "${version_header}" OR NOT EXISTS "${library}")
    message(FATAL_ERROR "ITCH_LIBURING_ROOT has no liburing: ${version_header} or ${library} is missing")
  endif()
  file(STRINGS "${version_header}" version_lines REGEX "#define IO_URING_VERSION_(MAJOR|MINOR)")
  string(REGEX MATCH "MAJOR[ \t]+([0-9]+)" unused "${version_lines}")
  set(major "${CMAKE_MATCH_1}")
  string(REGEX MATCH "MINOR[ \t]+([0-9]+)" unused "${version_lines}")
  set(minor "${CMAKE_MATCH_1}")
  if(NOT "${major}.${minor}" STREQUAL "${ITCH_LIBURING_VERSION}")
    message(FATAL_ERROR "liburing is ${major}.${minor}, not the pinned ${ITCH_LIBURING_VERSION}")
  endif()
  add_library(liburing::liburing STATIC IMPORTED GLOBAL)
  set_target_properties(liburing::liburing PROPERTIES
    IMPORTED_LOCATION "${library}"
    INTERFACE_SYSTEM_INCLUDE_DIRECTORIES "${ITCH_LIBURING_ROOT}/include"
    INTERFACE_INCLUDE_DIRECTORIES "${ITCH_LIBURING_ROOT}/include")
  message(STATUS "liburing: ${major}.${minor}")
endfunction()

# The receive modes a target that includes bench/udp/modes.hpp has: S2 to S4 on Linux, and
# U1 to U5 where -DITCH_LIBURING_ROOT made liburing::liburing. macOS and Emscripten get
# neither, and S1 alone, with no code change.
function(itch_udp_modes target)
  if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    target_compile_definitions(${target} PRIVATE ITCH_UDP_SOCKET_MODES=1)
  endif()
  if(TARGET liburing::liburing)
    target_compile_definitions(${target} PRIVATE ITCH_UDP_RING_MODES=1)
    target_link_libraries(${target} PRIVATE liburing::liburing)
  endif()
endfunction()

# mimalloc as itch::mimalloc: the static library, with its override of malloc, its macOS
# interpose and zone, and its architecture flags all off, so that it serves only the arm
# that calls it and the default build keeps no -march. With ITCH_PREBUILT_DEPS a scratch
# tree imports the preset's copy instead of fetching one, or goes without the arm if that
# preset never built it.
function(itch_require_mimalloc)
  if(ITCH_PREBUILT_DEPS)
    file(GLOB itch_mimalloc_library "${ITCH_PREBUILT_DEPS}/_deps/mimalloc-build/libmimalloc*.a")
    if(NOT itch_mimalloc_library)
      message(STATUS "mimalloc: off, ${ITCH_PREBUILT_DEPS} holds no build of it")
      return()
    endif()
    list(GET itch_mimalloc_library 0 itch_mimalloc_library)
    add_library(itch_mimalloc_imported STATIC IMPORTED GLOBAL)
    set_target_properties(itch_mimalloc_imported PROPERTIES
      IMPORTED_LOCATION "${itch_mimalloc_library}"
      INTERFACE_SYSTEM_INCLUDE_DIRECTORIES "${ITCH_PREBUILT_DEPS}/_deps/mimalloc-src/include"
      INTERFACE_INCLUDE_DIRECTORIES "${ITCH_PREBUILT_DEPS}/_deps/mimalloc-src/include")
    set(itch_mimalloc_target itch_mimalloc_imported)
  else()
    enable_language(C)
    foreach(off MI_OVERRIDE MI_OSX_INTERPOSE MI_OSX_ZONE MI_BUILD_SHARED MI_BUILD_OBJECT
                MI_BUILD_TESTS MI_OPT_ARCH)
      set(${off} OFF CACHE BOOL "" FORCE)
    endforeach()
    set(MI_NO_OPT_ARCH ON CACHE BOOL "" FORCE)
    set(MI_BUILD_STATIC ON CACHE BOOL "" FORCE)
    FetchContent_MakeAvailable(mimalloc)
    set(itch_mimalloc_target mimalloc-static)
  endif()
  add_library(itch_mimalloc INTERFACE)
  add_library(itch::mimalloc ALIAS itch_mimalloc)
  target_link_libraries(itch_mimalloc INTERFACE ${itch_mimalloc_target})
  target_compile_definitions(itch_mimalloc INTERFACE ITCH_HAVE_MIMALLOC)
  message(STATUS "mimalloc: v3.5.3")
endfunction()
