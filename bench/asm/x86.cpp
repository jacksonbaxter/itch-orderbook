// E12: the three x86-64 kernels, each function in its kernel's target region.
#include "functions.hpp"
#include "itch/kernels/avx2.hpp"
#include "itch/kernels/avx512vbmi.hpp"
#include "itch/kernels/ssse3.hpp"

ITCH_E12_FUNCTIONS(e12_ssse3, itch::kernels::ssse3::Kernel, [[gnu::target("ssse3")]])
ITCH_E12_FUNCTIONS(e12_avx2, itch::kernels::avx2::Kernel, [[gnu::target("avx2")]])
ITCH_E12_FUNCTIONS(e12_avx512vbmi, itch::kernels::avx512vbmi::Kernel,
                   [[gnu::target("avx512f,avx512bw,avx512vl,avx512vbmi")]])
