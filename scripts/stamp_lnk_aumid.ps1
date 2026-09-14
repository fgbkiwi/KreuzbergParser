# Stamp System.AppUserModel.ID on a .lnk so Windows taskbar pins use this
# shortcut instead of the bare flet.exe child process.
param(
    [Parameter(Mandatory = $true)]
    [string]$LnkPath,

    [string]$Aumid = "KiwiDown.App"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $LnkPath)) {
    throw "Shortcut not found: $LnkPath"
}

if (-not ([System.Management.Automation.PSTypeName]"KiwiDownLnkAumidStamp").Type) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class KiwiDownLnkAumidStamp {
  [DllImport("shell32.dll", CharSet=CharSet.Unicode, PreserveSig=false)]
  static extern void SHGetPropertyStoreFromParsingName(
    string path, IntPtr bc, uint flags, [In] ref Guid riid, out IPropertyStore store);
  [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
   Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
  interface IPropertyStore {
    uint GetCount(out uint cProps);
    uint GetAt(uint iProp, out PropertyKey pkey);
    uint GetValue(ref PropertyKey key, out PropVariant pv);
    uint SetValue(ref PropertyKey key, ref PropVariant pv);
    uint Commit();
  }
  [StructLayout(LayoutKind.Sequential, Pack=4)]
  struct PropertyKey { public Guid fmtid; public uint pid; }
  [StructLayout(LayoutKind.Sequential)]
  struct PropVariant {
    public ushort vt; public ushort r1,r2,r3; public IntPtr data; public int data2;
  }
  public static void Set(string path, string aumid) {
    Guid iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
    IPropertyStore store;
    SHGetPropertyStoreFromParsingName(path, IntPtr.Zero, 2, ref iid, out store);
    var key = new PropertyKey {
      fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5
    };
    var pv = new PropVariant { vt = 31, data = Marshal.StringToCoTaskMemUni(aumid) };
    try {
      store.SetValue(ref key, ref pv);
      store.Commit();
    } finally {
      Marshal.FreeCoTaskMem(pv.data);
    }
  }
}
"@
}

[KiwiDownLnkAumidStamp]::Set($LnkPath, $Aumid)
