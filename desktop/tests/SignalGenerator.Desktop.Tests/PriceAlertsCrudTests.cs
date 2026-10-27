using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using SignalGenerator.Desktop.Models;
using SignalGenerator.Desktop.Services;
using SignalGenerator.Desktop.ViewModels;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class PriceAlertsCrudTests
{
    private class MockAlertApiClient : TestMockApiClient
    {
        public List<AlertItemDto> AlertStore { get; } = new()
        {
            new()
            {
                AlertId = "pa_001",
                Symbol = "BTCUSDT",
                TargetPrice = 85000m,
                Direction = "PRICE_ABOVE",
                PriceSource = "MARK_PRICE",
                AlertType = "ONE_OFF",
                IsActive = true,
                Status = "ACTIVE",
                Armed = true,
                CurrentPrice = 80600m,
                DistancePct = 5.46m,
                Note = "Breakout test target"
            },
            new()
            {
                AlertId = "pa_002",
                Symbol = "ETHUSDT",
                TargetPrice = 2400m,
                Direction = "PRICE_BELOW",
                PriceSource = "MARK_PRICE",
                AlertType = "RECURRING",
                IsActive = false,
                Status = "DISABLED",
                Armed = false,
                CurrentPrice = 2650m,
                DistancePct = 9.43m,
                Note = "Support dip entry"
            }
        };

        public List<AlertEventDto> EventStore { get; } = new()
        {
            new()
            {
                EventId = 101,
                AlertId = "pa_001",
                Symbol = "BTCUSDT",
                TargetPrice = 80000m,
                TriggerPrice = 80150m,
                Direction = "PRICE_ABOVE",
                PriceSource = "MARK_PRICE",
                TriggeredAt = "2026-10-27T18:00:00Z",
                Acknowledged = false,
                DiscordStatus = "SENT",
                Note = "Initial alert breach"
            }
        };

        public bool FailOnCreate { get; set; }
        public bool FailOnUpdate { get; set; }
        public bool FailOnDelete { get; set; }
        public bool FailOnToggle { get; set; }

        public override Task<ApiResult<List<AlertItemDto>>> GetAlertsAsync(string? symbol = null, bool? isActive = null, CancellationToken ct = default)
        {
            return Task.FromResult(ApiResult<List<AlertItemDto>>.Success(new List<AlertItemDto>(AlertStore)));
        }

        public override Task<ApiResult<AlertItemDto>> CreateAlertAsync(AlertCreateRequestDto req, CancellationToken ct = default)
        {
            if (FailOnCreate)
            {
                return Task.FromResult(ApiResult<AlertItemDto>.Failure("Simulated backend creation error.", 400));
            }

            var item = new AlertItemDto
            {
                AlertId = "pa_" + Guid.NewGuid().ToString("N")[..6],
                Symbol = req.Symbol,
                TargetPrice = req.TargetPrice,
                Direction = req.Direction,
                PriceSource = req.PriceSource,
                AlertType = req.AlertType,
                Note = req.Note,
                IsActive = true,
                Status = "ACTIVE",
                Armed = true
            };
            AlertStore.Add(item);
            return Task.FromResult(ApiResult<AlertItemDto>.Success(item));
        }

        public override Task<ApiResult<AlertItemDto>> UpdateAlertAsync(string alertId, AlertUpdateRequestDto req, CancellationToken ct = default)
        {
            if (FailOnUpdate)
            {
                return Task.FromResult(ApiResult<AlertItemDto>.Failure("Simulated backend update failure.", 400));
            }

            var existing = AlertStore.Find(a => a.AlertId == alertId);
            if (existing == null)
            {
                return Task.FromResult(ApiResult<AlertItemDto>.Failure("Alert not found.", 404));
            }

            if (req.TargetPrice.HasValue) existing.TargetPrice = req.TargetPrice.Value;
            if (!string.IsNullOrWhiteSpace(req.Direction)) existing.Direction = req.Direction;
            if (!string.IsNullOrWhiteSpace(req.PriceSource)) existing.PriceSource = req.PriceSource;
            if (!string.IsNullOrWhiteSpace(req.AlertType)) existing.AlertType = req.AlertType;
            if (req.Note != null) existing.Note = req.Note;
            if (req.IsActive.HasValue) existing.IsActive = req.IsActive.Value;

            return Task.FromResult(ApiResult<AlertItemDto>.Success(existing));
        }

        public override Task<ApiResult<AlertItemDto>> ToggleAlertAsync(string alertId, CancellationToken ct = default)
        {
            if (FailOnToggle)
            {
                return Task.FromResult(ApiResult<AlertItemDto>.Failure("Simulated toggle failure.", 500));
            }

            var existing = AlertStore.Find(a => a.AlertId == alertId);
            if (existing == null)
            {
                return Task.FromResult(ApiResult<AlertItemDto>.Failure("Alert not found.", 404));
            }

            existing.IsActive = !existing.IsActive;
            existing.Status = existing.IsActive ? "ACTIVE" : "DISABLED";
            return Task.FromResult(ApiResult<AlertItemDto>.Success(existing));
        }

        public override Task<ApiResult<bool>> DeleteAlertAsync(string alertId, CancellationToken ct = default)
        {
            if (FailOnDelete)
            {
                return Task.FromResult(ApiResult<bool>.Failure("Simulated delete failure.", 404));
            }

            var removed = AlertStore.RemoveAll(a => a.AlertId == alertId) > 0;
            return Task.FromResult(ApiResult<bool>.Success(removed));
        }

        public override Task<ApiResult<List<AlertEventDto>>> GetAlertHistoryAsync(int limit = 50, bool unackOnly = false, CancellationToken ct = default)
        {
            return Task.FromResult(ApiResult<List<AlertEventDto>>.Success(new List<AlertEventDto>(EventStore)));
        }

        public override Task<ApiResult<AlertStatusDto>> GetAlertStatusAsync(CancellationToken ct = default)
        {
            return Task.FromResult(ApiResult<AlertStatusDto>.Success(new AlertStatusDto
            {
                TotalAlerts = AlertStore.Count,
                ActiveAlerts = AlertStore.FindAll(a => a.IsActive).Count,
                TriggeredAlerts = 0,
                StaleAlerts = 0
            }));
        }
    }

    [Fact]
    public async Task LoadAlertsAsync_LoadsStoredAlertsAndComputesTelemetry()
    {
        var client = new MockAlertApiClient();
        var vm = new AlertsViewModel(client);

        await vm.LoadAlertsAsync();

        Assert.Equal(2, vm.Alerts.Count);
        Assert.Equal(2, vm.TotalAlertsCount);
        Assert.Equal(1, vm.ActiveAlertsCount);
    }

    [Fact]
    public async Task CreateAlert_WithInvalidInputs_ShowsValidationMessageWithoutSubmitting()
    {
        var client = new MockAlertApiClient();
        var vm = new AlertsViewModel(client);

        vm.OpenCreateForm();
        Assert.True(vm.IsFormOpen);

        // Missing symbol
        vm.FormSymbol = "";
        vm.FormTargetPriceText = "100.5";
        await vm.SubmitFormAsync();
        Assert.Contains("Symbol is required", vm.FormValidationMessage);
        Assert.Equal(2, client.AlertStore.Count);

        // Negative price
        vm.FormSymbol = "SOLUSDT";
        vm.FormTargetPriceText = "-50";
        await vm.SubmitFormAsync();
        Assert.Contains("Target price must be a valid positive number", vm.FormValidationMessage);
        Assert.Equal(2, client.AlertStore.Count);
    }

    [Fact]
    public async Task CreateAlert_WithValidInput_PersistsAlertAndRefreshesList()
    {
        var client = new MockAlertApiClient();
        var vm = new AlertsViewModel(client);

        vm.OpenCreateForm();
        vm.FormSymbol = "SOLUSDT";
        vm.FormTargetPriceText = "225.5000";
        vm.FormDirection = "PRICE_ABOVE";
        vm.FormNote = "Take profit target";

        await vm.SubmitFormAsync();

        Assert.False(vm.IsFormOpen);
        Assert.Contains("ALERT CREATED", vm.SuccessMessage);
        Assert.Equal(3, vm.Alerts.Count);
        Assert.Contains(vm.Alerts, a => a.Symbol == "SOLUSDT" && a.TargetPrice == 225.5m);
    }

    [Fact]
    public async Task UpdateAlert_PopulatesExistingValues_AndPersistsModifications()
    {
        var client = new MockAlertApiClient();
        var vm = new AlertsViewModel(client);
        await vm.LoadAlertsAsync();

        var btcAlert = vm.Alerts[0];
        vm.OpenEditForm(btcAlert);

        Assert.True(vm.IsFormOpen);
        Assert.Equal("EDIT", vm.FormMode);
        Assert.Equal("pa_001", vm.FormAlertId);
        Assert.Equal("85000.0000", vm.FormTargetPriceText);

        // Update target price to 89000
        vm.FormTargetPriceText = "89000.0000";
        await vm.SubmitFormAsync();

        Assert.False(vm.IsFormOpen);
        Assert.Contains("ALERT UPDATED", vm.SuccessMessage);
        Assert.Equal(89000m, client.AlertStore[0].TargetPrice);
    }

    [Fact]
    public async Task ToggleAlert_TogglesActiveStateAndStatus()
    {
        var client = new MockAlertApiClient();
        var vm = new AlertsViewModel(client);
        await vm.LoadAlertsAsync();

        var btcAlert = vm.Alerts[0];
        Assert.True(btcAlert.IsActive);

        await vm.ToggleAlertAsync(btcAlert);
        Assert.False(client.AlertStore[0].IsActive);
        Assert.Equal("DISABLED", client.AlertStore[0].Status);
    }

    [Fact]
    public async Task DeleteAlert_PromptAndConfirmation_DeletesRecordAndRefreshes()
    {
        var client = new MockAlertApiClient();
        var vm = new AlertsViewModel(client);
        await vm.LoadAlertsAsync();

        var ethAlert = vm.Alerts[1];
        vm.PromptDeleteAlert(ethAlert);

        Assert.True(vm.IsDeleteConfirmOpen);
        Assert.Equal("pa_002", vm.DeletingAlertId);
        Assert.Contains("ETHUSDT", vm.DeletingAlertSummary);

        // Confirm deletion
        await vm.ConfirmDeleteAsync();

        Assert.False(vm.IsDeleteConfirmOpen);
        Assert.Single(vm.Alerts);
        Assert.Equal("pa_001", vm.Alerts[0].AlertId);
        Assert.Contains("ALERT DELETED", vm.SuccessMessage);
    }

    [Fact]
    public async Task Filtering_ByConditionStatusAndSymbol()
    {
        var client = new MockAlertApiClient();
        var vm = new AlertsViewModel(client);
        await vm.LoadAlertsAsync();

        // Filter by symbol
        vm.SearchFilter = "ETH";
        Assert.Single(vm.Alerts);
        Assert.Equal("ETHUSDT", vm.Alerts[0].Symbol);

        vm.SearchFilter = string.Empty;

        // Filter by Condition
        vm.SelectedConditionFilter = "PRICE_ABOVE";
        Assert.Single(vm.Alerts);
        Assert.Equal("BTCUSDT", vm.Alerts[0].Symbol);

        // Filter by Status
        vm.SelectedConditionFilter = "ALL";
        vm.SelectedStatusFilter = "DISABLED";
        Assert.Single(vm.Alerts);
        Assert.Equal("ETHUSDT", vm.Alerts[0].Symbol);
    }

    [Fact]
    public async Task TriggerHistory_LoadsAndPopulatesEvents()
    {
        var client = new MockAlertApiClient();
        var vm = new AlertsViewModel(client);

        vm.SelectTab("HISTORY");
        Assert.True(vm.IsHistoryTab);
        Assert.Single(vm.HistoryEvents);
        Assert.Equal("BTCUSDT", vm.HistoryEvents[0].Symbol);
        Assert.Equal(80150m, vm.HistoryEvents[0].TriggerPrice);
    }
}
