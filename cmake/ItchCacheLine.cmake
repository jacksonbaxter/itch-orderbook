# The cache line the SPSC ring pads to (include/itch/spsc.hpp), one constant for the
# build's target, chosen here because parser code holds no conditional compilation
# (scripts/gates.py). 128 on Apple silicon (LC-21), 64 on x86-64 (LC-24) and on other
# arm64 cores. A target with no measured size gets none, and spsc.hpp does not compile
# there. tests/test_spsc.cpp checks the value against the compiler's target and against
# the line size the system reports.
set(itch_target_arch "${CMAKE_SYSTEM_PROCESSOR}")
if(APPLE AND CMAKE_OSX_ARCHITECTURES)
  set(itch_target_arch "${CMAKE_OSX_ARCHITECTURES}")
endif()
if(APPLE AND itch_target_arch MATCHES "^(arm64|aarch64)$")
  set(ITCH_CACHE_LINE 128)
elseif(itch_target_arch MATCHES "^(x86_64|AMD64|amd64)$")
  set(ITCH_CACHE_LINE 64)
elseif(itch_target_arch MATCHES "^(arm64|aarch64)$")
  set(ITCH_CACHE_LINE 64)
else()
  set(ITCH_CACHE_LINE "")
endif()
