open_project kernel3
set_top kernel3
add_files "src/kernel_kernel.cpp"
add_files "src/kernel_kernel.h"
add_files -tb "src/kernel_host.cpp"

open_solution solution

#u250
set_part xcu250-figd2104-2L-e

# u280
#set_part xcu280-fsvh2892-2L-e

# 300 MHz
create_clock -period 3.333

# config_dataflow -strict_mode warning
# set_clock_uncertainty 27.000000%
# config_rtl -enable_maxiConservative=1
config_interface -m_axi_addr64

# to enable integration with Vitis
config_sdx -target xocc

csynth_design

close_project
exit
