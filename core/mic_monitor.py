"""
Windows CoreAudio default capture endpoint monitor for R.O.S.E.

Detects OS/hardware microphone mute state via IAudioEndpointVolume without
mutating hardware settings silently; provides an explicit user-triggered
unmute helper for HUD alert actions.
"""

from __future__ import annotations

from ctypes import HRESULT, POINTER, c_float, c_int, c_uint, c_void_p, pointer
from ctypes.wintypes import BOOL, DWORD, LPCWSTR, LPWSTR

try:
    import comtypes
    from comtypes import COMMETHOD, GUID, IUnknown
    _HAS_COMTYPES = True
except Exception:
    comtypes = None  # type: ignore
    _HAS_COMTYPES = False


if _HAS_COMTYPES:
    _CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    _IID_IMMDeviceEnumerator = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _IID_IAudioEndpointVolume = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")

    class _IAudioEndpointVolume(IUnknown):
        _iid_ = _IID_IAudioEndpointVolume
        _methods_ = [
            COMMETHOD([], HRESULT, "RegisterControlChangeNotify", (["in"], c_void_p, "pNotify")),
            COMMETHOD([], HRESULT, "UnregisterControlChangeNotify", (["in"], c_void_p, "pNotify")),
            COMMETHOD([], HRESULT, "GetChannelCount", (["out"], POINTER(c_uint), "pnChannelCount")),
            COMMETHOD([], HRESULT, "SetMasterVolumeLevel", (["in"], c_float, "fLevelDB"), (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "SetMasterVolumeLevelScalar", (["in"], c_float, "fLevel"), (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "GetMasterVolumeLevel", (["out"], POINTER(c_float), "pfLevelDB")),
            COMMETHOD([], HRESULT, "GetMasterVolumeLevelScalar", (["out"], POINTER(c_float), "pfLevel")),
            COMMETHOD([], HRESULT, "SetChannelVolumeLevel", (["in"], c_uint, "nChannel"), (["in"], c_float, "fLevelDB"), (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "SetChannelVolumeLevelScalar", (["in"], c_uint, "nChannel"), (["in"], c_float, "fLevel"), (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "GetChannelVolumeLevel", (["in"], c_uint, "nChannel"), (["out"], POINTER(c_float), "pfLevelDB")),
            COMMETHOD([], HRESULT, "GetChannelVolumeLevelScalar", (["in"], c_uint, "nChannel"), (["out"], POINTER(c_float), "pfLevel")),
            COMMETHOD([], HRESULT, "SetMute", (["in"], BOOL, "bMute"), (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "GetMute", (["out"], POINTER(BOOL), "pbMute")),
            COMMETHOD([], HRESULT, "GetVolumeStepInfo", (["out"], POINTER(c_uint), "pnStep"), (["out"], POINTER(c_uint), "pnStepCount")),
            COMMETHOD([], HRESULT, "VolumeStepUp", (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "VolumeStepDown", (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "QueryHardwareSupport", (["out"], POINTER(DWORD), "pdwHardwareSupportMask")),
            COMMETHOD([], HRESULT, "GetVolumeRange", (["out"], POINTER(c_float), "pfMin"), (["out"], POINTER(c_float), "pfMax"), (["out"], POINTER(c_float), "pfInc")),
        ]

    class _IMMDevice(IUnknown):
        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Activate",
                      (["in"], POINTER(GUID), "iid"),
                      (["in"], DWORD, "dwClsCtx"),
                      (["in"], c_void_p, "pActivationParams"),
                      (["out"], POINTER(POINTER(IUnknown)), "ppInterface")),
            COMMETHOD([], HRESULT, "OpenPropertyStore", (["in"], DWORD, "stgmAccess"), (["out"], POINTER(c_void_p), "ppProperties")),
            COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(LPWSTR), "ppstrId")),
            COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(DWORD), "pdwState")),
        ]

    class _IMMDeviceEnumerator(IUnknown):
        _iid_ = _IID_IMMDeviceEnumerator
        _methods_ = [
            COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], c_int, "dataFlow"), (["in"], DWORD, "dwStateMask"), (["out"], POINTER(c_void_p), "ppDevices")),
            COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], c_int, "dataFlow"), (["in"], c_int, "role"), (["out"], POINTER(POINTER(_IMMDevice)), "ppEndpoint")),
            COMMETHOD([], HRESULT, "GetDevice", (["in"], LPCWSTR, "pwstrId"), (["out"], POINTER(POINTER(_IMMDevice)), "ppDevice")),
            COMMETHOD([], HRESULT, "RegisterEndpointNotificationCallback", (["in"], c_void_p, "pClient")),
            COMMETHOD([], HRESULT, "UnregisterEndpointNotificationCallback", (["in"], c_void_p, "pClient")),
        ]


def _with_default_capture_endpoint(fn):
    """Run `fn(endpoint_volume)` on Windows default capture device with COM cleanup."""
    if not _HAS_COMTYPES:
        return None
    coinit_done = False
    try:
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            coinit_done = True
        except OSError:
            # Already initialized in STA (e.g. Qt main thread); proceed directly
            coinit_done = False

        enumerator = comtypes.CoCreateInstance(
            _CLSID_MMDeviceEnumerator,
            interface=_IMMDeviceEnumerator,
            clsctx=comtypes.CLSCTX_ALL,
        )
        # dataFlow=1 (eCapture), role=1 (eCommunications) -> fallback role=0 (eConsole)
        device = None
        for role in (1, 0):
            try:
                device = enumerator.GetDefaultAudioEndpoint(1, role)
                if device:
                    break
            except Exception:
                continue
        if not device:
            return None

        unk = device.Activate(pointer(_IID_IAudioEndpointVolume), comtypes.CLSCTX_ALL, None)
        vol_iface = unk.QueryInterface(_IAudioEndpointVolume)
        return fn(vol_iface)
    except Exception:
        return None
    finally:
        if coinit_done:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass


def get_default_mic_state() -> dict:
    """Return {'available': bool, 'muted': Optional[bool], 'volume': Optional[float]}."""
    def _read(ep):
        muted = bool(ep.GetMute())
        scalar = float(ep.GetMasterVolumeLevelScalar())
        return {"available": True, "muted": muted, "volume": round(scalar, 3)}

    res = _with_default_capture_endpoint(_read)
    if res is None:
        return {"available": False, "muted": None, "volume": None}
    return res


def set_default_mic_muted(muted: bool) -> bool:
    """Explicitly set OS capture endpoint mute state (call only on explicit user action)."""
    def _write(ep):
        ep.SetMute(1 if muted else 0, None)
        return bool(ep.GetMute()) == bool(muted)

    return bool(_with_default_capture_endpoint(_write))

