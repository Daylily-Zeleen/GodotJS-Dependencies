include_guard(GLOBAL)

get_filename_component(_LIBNODE_WINDOWS_DIR "${CMAKE_CURRENT_LIST_FILE}" DIRECTORY)
get_filename_component(_LIBNODE_PACKAGE_DIR "${_LIBNODE_WINDOWS_DIR}/../.." ABSOLUTE)

add_library(libnode::libnode STATIC IMPORTED GLOBAL)
set_target_properties(libnode::libnode PROPERTIES
  IMPORTED_LOCATION "${_LIBNODE_WINDOWS_DIR}/libnode.lib"
  INTERFACE_INCLUDE_DIRECTORIES "${_LIBNODE_PACKAGE_DIR}/include"
  INTERFACE_LINK_LIBRARIES "Dbghelp;Psapi;Winmm;Ws2_32;Advapi32;Crypt32;Ole32;Iphlpapi;Shell32;User32;Userenv;Uuid"
  INTERFACE_LINK_OPTIONS "/WHOLEARCHIVE:$<TARGET_FILE:libnode::libnode>"
)
set_property(TARGET libnode::libnode PROPERTY INTERFACE_COMPILE_OPTIONS
  "$<$<COMPILE_LANG_AND_ID:CXX,MSVC>:/std:c++20>"
  "$<$<COMPILE_LANG_AND_ID:CXX,MSVC>:/Zc:__cplusplus>"
  "$<$<COMPILE_LANG_AND_ID:CXX,MSVC>:/MT>"
  "$<$<COMPILE_LANG_AND_ID:CXX,Clang>:-std=c++20>"
)
