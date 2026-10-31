# Compile options for everything we own, and the sanitizer flags for the build.
#
# Sanitizers apply to the whole build, third-party code included, because a
# partly instrumented binary gives false reports. Warnings apply only to our
# targets, through itch_apply_options().

if(ITCH_SANITIZE)
  add_compile_options(
    "-fsanitize=${ITCH_SANITIZE}"
    -fno-sanitize-recover=all
    -fno-omit-frame-pointer)
  add_link_options("-fsanitize=${ITCH_SANITIZE}")
  if(ITCH_SANITIZE MATCHES "undefined")
    # Part of -fsanitize=undefined today. Named so that it cannot quietly go.
    add_compile_options(-fsanitize=alignment)
    add_link_options(-fsanitize=alignment)
  endif()
  # Bounds checks inside the standard library, on in every sanitizer build.
  add_compile_definitions(_LIBCPP_HARDENING_MODE=_LIBCPP_HARDENING_MODE_DEBUG)
endif()

if(ITCH_FUZZ)
  add_compile_options(-fsanitize=fuzzer-no-link)
endif()

# On macOS a toolchain's libc++ headers can be newer than the system dylib, and code
# using std::variant then fails to link. Where the compiler has its own static
# libc++, link that instead. The directory is found from the compiler the environment
# chose, or from ITCH_LIBCXX_DIR. Set ITCH_LIBCXX_DIR to an empty value to opt out.
#
# std:: functions and vtables stay unexported. libSystem loads the system libc++ into every process,
# and dyld would otherwise coalesce the binary's weak std:: definitions onto that copy,
# leaving two libc++ copies freeing each other's locale objects at startup.
if(APPLE AND NOT CMAKE_CXX_COMPILER_ID STREQUAL "AppleClang")
  if(DEFINED ENV{ITCH_LIBCXX_DIR})
    set(itch_libcxx_dir "$ENV{ITCH_LIBCXX_DIR}")
  else()
    cmake_path(GET CMAKE_CXX_COMPILER PARENT_PATH itch_compiler_bin)
    cmake_path(GET itch_compiler_bin PARENT_PATH itch_compiler_root)
    set(itch_libcxx_dir "${itch_compiler_root}/lib")
  endif()
  if(itch_libcxx_dir AND EXISTS "${itch_libcxx_dir}/libc++.a")
    add_link_options(-nostdlib++
      "-Wl,${itch_libcxx_dir}/libc++.a" "-Wl,${itch_libcxx_dir}/libc++abi.a"
      "-Wl,-unexported_symbol,__ZNSt3__1*" "-Wl,-unexported_symbol,__ZNKSt3__1*"
      "-Wl,-unexported_symbol,__ZT?NSt3__1*"
      "-Wl,-unexported_symbol,__ZTv*NSt3__1*" "-Wl,-unexported_symbol,__ZTh*NSt3__1*")
  endif()
endif()

add_library(itch_warnings INTERFACE)
target_compile_options(itch_warnings INTERFACE
  -Wall -Wextra -Werror
  -Wconversion -Wsign-conversion -Wshadow -Wold-style-cast)

# Warnings and tidy for one of our targets. Call it on every target we add.
function(itch_apply_options target)
  get_target_property(kind ${target} TYPE)
  if(kind STREQUAL "INTERFACE_LIBRARY")
    target_link_libraries(${target} INTERFACE itch_warnings)
  else()
    target_link_libraries(${target} PRIVATE itch_warnings)
    if(ITCH_TIDY)
      set_target_properties(${target} PROPERTIES CXX_CLANG_TIDY "${ITCH_CLANG_TIDY_COMMAND}")
    endif()
  endif()
endfunction()
