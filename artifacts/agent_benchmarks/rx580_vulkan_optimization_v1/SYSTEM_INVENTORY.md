# RX 580 / Vulkan Runtime System Inventory

Captured: `2026-08-23T03:41:50.251+00:00`

## Frozen inference identity

- Model: `qwen3.5-4b@q6_k`
- GGUF: `D:\FromC\Users\disti\.lmstudio\models\lmstudio-community\Qwen3.5-4B-GGUF\Qwen3.5-4B-Q6_K.gguf`
- GGUF SHA-256: `12a6d0136484ecbd85f91860924b1bf571da66e2cebfa507522efc01f9b7d1d9`
- llama.cpp binary: `C:\Users\disti\.lmstudio\extensions\backends\llama.cpp-win-x86_64-vulkan-avx2-2.28.2\llama-server.exe`
- llama.cpp binary SHA-256: `b7346fdf06749e2b4265320b4d080e522dce14f77d3e263651ef91f12c7dc43c`
- llama.cpp build: `fe2adf0`
- Vulkan backend manifest SHA-256: `220daa1d7061d0b4ef179ff421c116f255330a5aa072f6187b099a4f598d11f8`

## GPU and driver

```text
exit_code=0
STDOUT


Name                 : Meta Virtual Monitor
AdapterCompatibility : Meta Inc.
AdapterRAM           : 
PNPDeviceID          : ROOT\DISPLAY\0000
DriverVersion        : 15.52.32.370
DriverDate           : 4/22/2026 8:00:00 PM
Status               : OK
VideoModeDescription : 

Name                 : Radeon RX 580 Series
AdapterCompatibility : Advanced Micro Devices, Inc.
AdapterRAM           : 4293918720
PNPDeviceID          : PCI\VEN_1002&DEV_67DF&SUBSYS_34181462&REV_E7\4&1FC990D7&
                       0&0019
DriverVersion        : 31.0.21923.11000
DriverDate           : 6/30/2025 8:00:00 PM
Status               : OK
VideoModeDescription : 1920 x 1080 x 4294967296 colors





DeviceName    : Radeon RX 580 Series
DriverVersion : 31.0.21923.11000
DriverDate    : 6/30/2025 8:00:00 PM
Manufacturer  : Advanced Micro Devices, Inc.
InfName       : oem28.inf
Signer        : Microsoft Windows Hardware Compatibility Publisher




STDERR

```

## Windows, power plan, and scheduling

```text
exit_code=0
STDOUT


WindowsProductName : Windows 10 Pro
WindowsVersion     : 2009
OsBuildNumber      : 26200
OsArchitecture     : 64-bit
CsName             : DESKTOP-VEMUEKR



Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)
Existing Power Schemes (* Active)
-----------------------------------
Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *
Power Scheme GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (High performance)
Power Scheme GUID: a1841308-3541-4fab-bc81-f71556f20b4a  (Power saver)

STDERR

exit_code=0
STDOUT


PSPath            : Microsoft.PowerShell.Core\Registry::HKEY_LOCAL_MACHINE\SYST
                    EM\CurrentControlSet\Control\GraphicsDrivers
HwSchMode         : 
UseGameDVR        : 
AppCaptureEnabled : 





PSPath            : Microsoft.PowerShell.Core\Registry::HKEY_CURRENT_USER\Softw
                    are\Microsoft\Windows\CurrentVersion\GameDVR
HwSchMode         : 
UseGameDVR        : 
AppCaptureEnabled : 1




    Power Setting GUID: 893dee8e-2bef-41e0-89c6-b55d0929964c  (Minimum 
processor state)
    Current AC Power Setting Index: 0x00000005
    Current DC Power Setting Index: 0x00000005
    Power Setting GUID: bc5038f7-23e0-4960-96da-33abaf5935ec  (Maximum 
processor state)
    Current AC Power Setting Index: 0x00000064
    Current DC Power Setting Index: 0x00000064



STDERR

```

## Active inference processes (API keys redacted)

```text
exit_code=0
STDOUT


ProcessId      : 18812
Name           : LM Studio.exe
ExecutablePath : C:\Users\disti\AppData\Local\Programs\LM Studio\LM Studio.exe
CommandLine    : "C:\Users\disti\AppData\Local\Programs\LM Studio\LM 
                 Studio.exe" --run-as-service

ProcessId      : 29044
Name           : LM Studio.exe
ExecutablePath : C:\Users\disti\AppData\Local\Programs\LM Studio\LM Studio.exe
CommandLine    : "C:\Users\disti\AppData\Local\Programs\LM Studio\LM 
                 Studio.exe" --type=crashpad-handler 
                 "--user-data-dir=C:\Users\disti\AppData\Roaming\LM Studio" 
                 /prefetch:4 --no-rate-limit --no-upload-gzip 
                 --monitor-self-annotation=ptype=crashpad-handler 
                 "--database=C:\Users\disti\AppData\Roaming\LM 
                 Studio\Crashpad" "--annotation=_productName=LM Studio" 
                 --annotation=_version=0.4.21+2 --annotation=plat=Win64 
                 --annotation=prod=Electron --annotation=ver=38.6.0 --initial-c
                 lient-data=0x4cc,0x4d0,0x4d4,0x44c,0xa8,0x7ff6264734d4,0x7ff62
                 64734e0,0x7ff6264734f0

ProcessId      : 29396
Name           : LM Studio.exe
ExecutablePath : C:\Users\disti\AppData\Local\Programs\LM Studio\LM Studio.exe
CommandLine    : "C:\Users\disti\AppData\Local\Programs\LM Studio\LM 
                 Studio.exe" --type=gpu-process 
                 --user-data-dir="C:\Users\disti\AppData\Roaming\LM Studio" --g
                 pu-preferences=SAAAAAAAAADgAAAIAAAAAAAAAAAAAGAAAQAAAAAAAAAEAAA
                 AAAAAAAAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAABAAAAAAAAAACAAAAAAAAAAIA
                 AAAAAAAAA== --field-trial-handle=1900,i,5314359063022587396,17
                 024037998411873744,262144 --enable-features=EnableTransparentH
                 wndEnlargement,PdfUseShowSaveFilePicker --disable-features=Scr
                 eenAIOCREnabled,SpareRendererForSitePerProcess,TraceSiteInstan
                 ceGetProcessCreation,WinDelaySpellcheckServiceInit 
                 --variations-seed-version --mojo-platform-channel-handle=1896 
                 /prefetch:2

ProcessId      : 29420
Name           : LM Studio.exe
ExecutablePath : C:\Users\disti\AppData\Local\Programs\LM Studio\LM Studio.exe
CommandLine    : "C:\Users\disti\AppData\Local\Programs\LM Studio\LM 
                 Studio.exe" --type=utility 
                 --utility-sub-type=network.mojom.NetworkService --lang=en-US 
                 --service-sandbox-type=none 
                 --user-data-dir="C:\Users\disti\AppData\Roaming\LM Studio" --f
                 ield-trial-handle=1900,i,5314359063022587396,17024037998411873
                 744,262144 --enable-features=EnableTransparentHwndEnlargement,
                 PdfUseShowSaveFilePicker --disable-features=ScreenAIOCREnabled
                 ,SpareRendererForSitePerProcess,TraceSiteInstanceGetProcessCre
                 ation,WinDelaySpellcheckServiceInit --variations-seed-version 
                 --mojo-platform-channel-handle=2104 /prefetch:11

ProcessId      : 29532
Name           : LM Studio.exe
ExecutablePath : C:\Users\disti\AppData\Local\Programs\LM Studio\LM Studio.exe
CommandLine    : "C:\Users\disti\AppData\Local\Programs\LM Studio\LM 
                 Studio.exe" --type=utility 
                 --utility-sub-type=node.mojom.NodeService --lang=en-US 
                 --service-sandbox-type=none 
                 --user-data-dir="C:\Users\disti\AppData\Roaming\LM Studio" --f
                 ield-trial-handle=1900,i,5314359063022587396,17024037998411873
                 744,262144 --enable-features=EnableTransparentHwndEnlargement,
                 PdfUseShowSaveFilePicker --disable-features=ScreenAIOCREnabled
                 ,SpareRendererForSitePerProcess,TraceSiteInstanceGetProcessCre
                 ation,WinDelaySpellcheckServiceInit --variations-seed-version 
                 --mojo-platform-channel-handle=2664 /prefetch:14

ProcessId      : 18020
Name           : llama-server.exe
ExecutablePath : C:\Users\disti\.lmstudio\extensions\backends\llama.cpp-win-x86
                 _64-vulkan-avx2-2.28.2\llama-server.exe
CommandLine    : C:\Users\disti\.lmstudio\extensions\backends\llama.cpp-win-x86
                 _64-vulkan-avx2-2.28.2\llama-server.exe --model C:\Users\disti
                 \.lmstudio\models\lmstudio-community\Qwen3.5-4B-GGUF\Qwen3.5-4
                 B-Q6_K.gguf --host 127.0.0.1 --port 53240 --api-key 
                 <redacted> --verbosity 3 
                 --no-webui --jinja --chat-template-file C:\Users\disti\AppData
                 \Local\Temp\lmstudio-llama-chat-template-OiK7lo\chat-template.
                 jinja --ctx-size 32768 --n-gpu-layers 999999 --n-cpu-moe 0 
                 --main-gpu 0 --tensor-split 0 --split-mode layer 
                 --ctx-checkpoints 32 --batch-size 2048 --ubatch-size 512 
                 --threads 6 --parallel 1 --cache-type-k f16 --cache-type-v 
                 f16 --mmproj C:/Users/disti/.lmstudio/models/lmstudio-communit
                 y/Qwen3.5-4B-GGUF/mmproj-Qwen3.5-4B-BF16.gguf --flash-attn on 
                 --kv-offload --kv-unified --load-mode mmap+mlock





STDERR

```

## Vulkan summary

`vulkaninfo` exit code: `0`

```text
==========
VULKANINFO
==========

Vulkan Instance Version: 1.3.301


Instance Extensions: count = 13
-------------------------------
VK_EXT_debug_report                    : extension revision 10
VK_EXT_debug_utils                     : extension revision 2
VK_EXT_swapchain_colorspace            : extension revision 4
VK_KHR_device_group_creation           : extension revision 1
VK_KHR_external_fence_capabilities     : extension revision 1
VK_KHR_external_memory_capabilities    : extension revision 1
VK_KHR_external_semaphore_capabilities : extension revision 1
VK_KHR_get_physical_device_properties2 : extension revision 2
VK_KHR_get_surface_capabilities2       : extension revision 1
VK_KHR_portability_enumeration         : extension revision 1
VK_KHR_surface                         : extension revision 25
VK_KHR_win32_surface                   : extension revision 6
VK_LUNARG_direct_driver_loading        : extension revision 1

Instance Layers: count = 3
--------------------------
VK_LAYER_AMD_switchable_graphics AMD switchable graphics layer                 1.3.260  version 1
VK_LAYER_EOS_Overlay             Vulkan overlay layer for Epic Online Services 1.2.136  version 1
VK_LAYER_OBS_HOOK                Open Broadcaster Software hook                1.3.216  version 1

Devices:
========
GPU0:
	apiVersion         = 1.3.260
	driverVersion      = 2.0.279
	vendorID           = 0x1002
	deviceID           = 0x67df
	deviceType         = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
	deviceName         = Radeon RX 580 Series
	driverID           = DRIVER_ID_AMD_PROPRIETARY
	driverName         = AMD proprietary driver
	driverInfo         = 25.8.1 (AMD proprietary shader compiler)
	conformanceVersion = 1.3.3.1
	deviceUUID         = 00000000-0800-0000-0000-000000000000
	driverUUID         = 414d442d-5749-4e2d-4452-560000000000

WARNING: [Loader Message] Code 0 : windows_read_data_files_in_registry: Registry lookup failed to get layer manifest files.
WARNING: [Loader Message] Code 0 : vkGetPhysicalDeviceSurfaceCapabilities2KHR: Emulation found unrecognized structure type in pSurfaceInfo->pNext - this struct will be ignored

```

The read-only Vulkan inventory reports the RX 580 as vendor `0x1002`, device `0x67df`, discrete GPU, AMD proprietary driver, Vulkan 1.3.260, driver info `25.8.1`, and 8192 MiB device memory with approximately 7402 MiB free at inventory time.

## llama.cpp device enumeration

```text

version: 1 (fe2adf0)
built with MSVC 19.44.35228.0 for x64

Available devices:
  Vulkan0: Radeon RX 580 Series (8192 MiB, 7402 MiB free)


```

## Backend manifest

```json
{
  "version": "2.28.2",
  "domains": [
    "llm",
    "embedding"
  ],
  "engine": "llama.cpp",
  "target_libraries": [
    {
      "name": "llm_engine_vulkan.node",
      "type": "llm_engine",
      "version": "0.1.2"
    },
    {
      "name": "liblmstudio_bindings_vulkan.node",
      "type": "liblmstudio",
      "version": "0.2.26"
    }
  ],
  "platform": "win",
  "cpu": {
    "architecture": "x86_64",
    "instruction_set_extensions": [
      "AVX2"
    ]
  },
  "gpu": {
    "framework": "Vulkan"
  },
  "supported_model_formats": [
    "gguf"
  ],
  "manifest_version": "4",
  "extension_type": "engine",
  "vendor_lib_package_names": [
    "win-llama-vulkan-vendor-v2"
  ],
  "minimum_lmstudio_version": "0.4.0+15",
  "minimum_llmster_version": "0.0.1+9",
  "engine_protocol_server": {
    "runtime_kind": "llama-server",
    "executable_relative_path": "llama-server.exe"
  },
  "name": "llama.cpp-win-x86_64-vulkan-avx2"
}
```

## Display/runtime metadata

```text
[["en",{"langKey":"en","displayName":"Vulkan llama.cpp (Windows)","description":"Vulkan accelerated llama.cpp engine","releaseNotes":[{"version":"2.28.2","releaseNotes":"- llama.cpp release b10252 (commit fe2adf0e7)\n"},{"version":"2.28.1","releaseNotes":"- llama.cpp release b10252 (commit fe2adf0e7)\n"},{"version":"2.28.0","releaseNotes":"- llama.cpp release b10252 (commit fe2adf0e7)\r\n"},{"version":"2.27.1","releaseNotes":"- llama.cpp release b10099 (commit 1a064ab09)\n"},{"version":"2.27.0","releaseNotes":"- llama.cpp release b10099 (commit 1a064ab09)\n"},{"version":"2.26.0","releaseNotes":"- llama.cpp release b10064 (commit 86d86ed43)\n"},{"version":"2.25.2","releaseNotes":"- llama.cpp release b9888 (commit cb295bf59)\n"},{"version":"2.25.1","releaseNotes":"- llama.cpp release b9888 (commit cb295bf59)\r\n"},{"version":"2.25.0","releaseNotes":"- llama.cpp release b9888 (commit cb295bf59)\n"},{"version":"2.24.0","releaseNotes":"- llama.cpp release b9851 (commit 0eca4d490)\n- Support for DeepSeek V4\n"},{"version":"2.23.1","releaseNotes":"- llama.cpp release b9660 (commit 7dad2f1a)\n- Fix classification of expert tensors in some MoE models"},{"version":"2.23.0","releaseNotes":"- llama.cpp release b9660 (commit 7dad2f1a)"},{"version":"2.22.0","releaseNotes":"- llama.cpp release b9601 (commit 4c65955)\n"},{"version":"2.21.0","releaseNotes":"- llama.cpp release b9562 (commit 8f83d6c)\n"},{"version":"2.20.1","releaseNotes":"- llama.cpp release b9496 (commit 94a220cd6)\n- Improved vision quality for Gemma 4 12B\n- Fixed loading errors for Gemma 4 12B and other Gemma 4 models\n"},{"version":"2.20.0","releaseNotes":"- llama.cpp release b9482 (commit 4fb16ec)\n- Support for Gemma 4 12b\n"},{"version":"2.19.1","releaseNotes":"- llama.cpp release b9482 (commit 4fb16ec)\n- Support for Step 3.7 Flash model\n"},{"version":"2.18.0","releaseNotes":"- llama.cpp release b9412 (commit 06d26df)\n"},{"version":"2.17.0","releaseNotes":"- llama.cpp release b9334 (commit 5190c2e)\n"},{"version":"2.16.0","releaseNotes":"- llama.cpp release b9265 (commit 5306f4b)\n- Beta release of MTP Speculative Decoding!\n  - Speeds up generation with models that include built-in multi-token prediction heads\n  - To try it out, download an MTP-capable model and enable \"MTP Speculative Decoding\" in the advanced model load settings\n"},{"version":"2.15.0","releaseNotes":"- llama.cpp release b9199 (commit 39cf5d6)\n- Beta release of MTP Speculative Decoding!\n  - Speeds up generation with models that include built-in multi-token prediction heads\n  - To try it out, download an MTP-capable model and enable \"MTP Speculative Decoding\" in the advanced model load settings\n"},{"version":"2.14.4","releaseNotes":"- llama.cpp release b8950 (commit 4414c04)\n- Beta release of the new LM Studio Engine Protocol!\n  - Enables more frequent updates of runtime engines, such as llama.cpp\n  - Get the LM Studio App Beta version 0.4.14+1 or later, and enable \"Use engine protocol runtimes\" in Developer settings to try it out\n"},{"version":"2.14.0","releaseNotes":"- llama.cpp release b8861 (commit cf8b0db)\n"},{"version":"2.13.0","releaseNotes":"- llama.cpp release b8733 (commit d6f3030)\n"},{"version":"2.12.0","releaseNotes":"- llama.cpp release b8679 (commit 94ca829)\n"},{"version":"2.11.0","releaseNotes":"- Gemma 4 improvements:\n  - Reduce frequency of gibberish output\n  - Reduce memory usage (KV cache)\n  - We are continuing to monitor and improve quality issues\n- llama.cpp release b8656 (commit 277ff5f)\n"},{"version":"2.10.1","releaseNotes":"- Gemma 4 support\n- Improve Gemma 4 31B output quality\n"},{"version":"2.10.0","releaseNotes":"- Gemma 4 support\n"},{"version":"2.9.0","releaseNotes":"- llama.cpp release b8429 (commit b486c17)\n"},{"version":"2.8.0","releaseNotes":"- llama.cpp release b8398 (commit 054d8b0)\n"},{"version":"2.7.1","releaseNotes":"- NVIDIA Nemotron 3 Super support\n- llama.cpp release b8275 (commit 6c770d16)\n"},{"version":"2.7.0","releaseNotes":"
```

## Initial GPU counters

```text
exit_code=0
STDOUT
{"engine":{"InstanceName":"pid_20608_luid_0x00000000_0x0000d23a_phys_0_eng_0_engtype_3d","CookedValue":1.2197350707515624},"adapter_memory":[{"Name":"luid_0x00000000_0x00011C95_phys_0","DedicatedUsage":0,"SharedUsage":8192,"TotalCommitted":5705728},{"Name":"luid_0x00000000_0x0000D23A_phys_0","DedicatedUsage":2167001088,"SharedUsage":19447808,"TotalCommitted":8126119936}],"process_memory":[{"Name":"pid_33152_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":78364672,"LocalUsage":14004224,"NonLocalUsage":237568},{"Name":"pid_14580_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":15659008,"LocalUsage":4505600,"NonLocalUsage":28672},{"Name":"pid_18020_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":1087868928,"LocalUsage":6336512,"NonLocalUsage":0},{"Name":"pid_19820_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":17285120,"LocalUsage":4243456,"NonLocalUsage":0},{"Name":"pid_21112_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":6004736,"LocalUsage":4218880,"NonLocalUsage":0},{"Name":"pid_17648_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":114855936,"LocalUsage":82497536,"NonLocalUsage":2920448},{"Name":"pid_3452_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":141672448,"LocalUsage":34967552,"NonLocalUsage":1474560},{"Name":"pid_31300_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":57765888,"LocalUsage":23654400,"NonLocalUsage":221184},{"Name":"pid_17288_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":5521408,"LocalUsage":4218880,"NonLocalUsage":0},{"Name":"pid_28128_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":12578816,"LocalUsage":4243456,"NonLocalUsage":0},{"Name":"pid_29396_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":4505600,"LocalUsage":4206592,"NonLocalUsage":0},{"Name":"pid_26288_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":14123008,"LocalUsage":4247552,"NonLocalUsage":0},{"Name":"pid_13976_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":40980480,"LocalUsage":4456448,"NonLocalUsage":0},{"Name":"pid_17236_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":25341952,"LocalUsage":4284416,"NonLocalUsage":0},{"Name":"pid_2756_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":4378624,"LocalUsage":4206592,"NonLocalUsage":0},{"Name":"pid_22828_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":6643712,"LocalUsage":4210688,"NonLocalUsage":0},{"Name":"pid_23744_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":4648960,"LocalUsage":4214784,"NonLocalUsage":0},{"Name":"pid_23348_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":6565888,"LocalUsage":4214784,"NonLocalUsage":0},{"Name":"pid_22672_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":4509696,"LocalUsage":4210688,"NonLocalUsage":0},{"Name":"pid_20608_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":28336128,"LocalUsage":8032256,"NonLocalUsage":3756032},{"Name":"pid_18632_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":4378624,"LocalUsage":4206592,"NonLocalUsage":0},{"Name":"pid_17828_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":4378624,"LocalUsage":4206592,"NonLocalUsage":0},{"Name":"pid_18036_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":48115712,"LocalUsage":4481024,"NonLocalUsage":0},{"Name":"pid_16828_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":74911744,"LocalUsage":42967040,"NonLocalUsage":20480},{"Name":"pid_14476_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":4505600,"LocalUsage":4206592,"NonLocalUsage":0},{"Name":"pid_9744_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":18063360,"LocalUsage":4255744,"NonLocalUsage":0},{"Name":"pid_3080_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":8527872,"LocalUsage":4251648,"NonLocalUsage":0},{"Name":"pid_3880_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":4648960,"LocalUsage":4214784,"NonLocalUsage":0},{"Name":"pid_7664_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":15831040,"LocalUsage":4263936,"NonLocalUsage":0},{"Name":"pid_14304_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":4648960,"LocalUsage":4214784,"NonLocalUsage":0},{"Name":"pid_12912_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":58126336,"LocalUsage":4386816,"NonLocalUsage":0},{"Name":"pid_11036_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":11395072,"LocalUsage":4235264,"NonLocalUsage":0},{"Name":"pid_10756_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":10633216,"LocalUsage":4231168,"NonLocalUsage":0},{"Name":"pid_9680_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":62316544,"LocalUsage":12607488,"NonLocalUsage":987136},{"Name":"pid_10072_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":4648960,"LocalUsage":4214784,"NonLocalUsage":0},{"Name":"pid_9792_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":4648960,"LocalUsage":4214784,"NonLocalUsage":0},{"Name":"pid_1136_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":298352640,"LocalUsage":54747136,"NonLocalUsage":450560},{"Name":"pid_1360_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":28614656,"LocalUsage":19832832,"NonLocalUsage":8826880},{"Name":"pid_4_luid_0x00000000_0x00011C95_phys_0","ProcessID":null,"DedicatedUsage":0,"LocalUsage":8192,"NonLocalUsage":0},{"Name":"pid_4_luid_0x00000000_0x0000D23A_phys_0","ProcessID":null,"DedicatedUsage":8388608,"LocalUsage":8388608,"NonLocalUsage":524288}]}

STDERR

```

Windows exposed GPU-engine utilization and adapter/process-memory counters, but no reliable RX 580 core-clock, memory-clock, or temperature counter through the collected interfaces. PCIe negotiated generation/width was not exposed by the collected Windows/Vulkan interfaces; this remains an inventory gap, not a normality claim.
