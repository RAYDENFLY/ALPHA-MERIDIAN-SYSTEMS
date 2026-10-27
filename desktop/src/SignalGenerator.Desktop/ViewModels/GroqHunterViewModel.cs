using CommunityToolkit.Mvvm.ComponentModel;

namespace SignalGenerator.Desktop.ViewModels;

public partial class GroqHunterViewModel : ViewModelBase
{
    [ObservableProperty]
    private string _groqStatus = "STANDBY";

    [ObservableProperty]
    private string _modelName = "llama-3.3-70b-versatile";

    [ObservableProperty]
    private string _stage1PrefilterStatus = "ACTIVE (0-Token Local Heuristic)";

    [ObservableProperty]
    private string _stage2SMCVerification = "ACTIVE (Dealing Range + Liquidity Sweep)";

    [ObservableProperty]
    private string _stage3LLMExecution = "STANDBY (Triggered on SMC Confluence)";

    [ObservableProperty]
    private string _stage4OrderProtection = "ACTIVE (Hard Zone Lock + Adaptive TP)";

    [ObservableProperty]
    private string _keyRotationStatus = "Healthy (Multi-Key Vault Ready)";
}
