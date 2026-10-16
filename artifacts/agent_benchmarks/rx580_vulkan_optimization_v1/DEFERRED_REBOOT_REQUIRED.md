# Deferred driver/reboot work

No driver or system package was installed, removed, downgraded, or upgraded.
No reboot, display reset, firmware change, Safe Mode operation, or remote-access change was performed.

## Current

- Windows display driver store: AMD Radeon RX 580 `31.0.21923.11000`
- Vulkan driver info: AMD proprietary `25.8.1`
- Current Vulkan backend: llama.cpp `fe2adf0` / LM Studio engine package 2.28.2

## Candidate for a later maintenance window

AMD lists Adrenalin `26.5.2` for Radeon RX 500-series Windows 10/11 on its RX 580 support page, with a Polaris/Vega release note. This is a candidate comparison point, not an instruction to install it during a remote session.

- Official support page: https://www.amd.com/en/support/downloads/drivers.html/graphics/radeon-600-500-400/radeon-rx-500-series/radeon-rx-580.html
- Official release notes: https://www.amd.com/en/resources/support-articles/release-notes/RN-RAD-WIN-26-5-2-POLARIS-VEGA.html

Potential benefit: updated Vulkan driver/shader compiler behavior could improve Polaris prompt kernels. Risks: driver replacement can require reboot, reset the display path, alter Vulkan compatibility, or strand remote access. Any comparison is therefore `DEFERRED_REBOOT_REQUIRED` and belongs to a maintenance window with physical/recovery access.

Status: `DEFERRED_REBOOT_REQUIRED`
