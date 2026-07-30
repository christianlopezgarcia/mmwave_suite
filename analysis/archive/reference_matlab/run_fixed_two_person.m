function out = run_fixed_two_person(varargin)
% RUN_FIXED_TWO_PERSON (UPDATED v2 - extended raw + overlay outputs)
% Extract RT, VT (per person), and toe envelopes for two-person walking.
% Uses EMPTY recording for background subtraction.
%
% radar_data shape: (F=576, R=3, C=128, S=280)
%   F = frames, R = rx antennas, C = chirps, S = samples
%
% Saves in walk folder (8 plots total):
%   RT_Raw_NoBgSub.png          -- full range, no bg subtraction
%   RT_Raw_BgSub.png            -- full range, bg subtracted, no tracking
%   RT_TwoPerson_Tracks.png     -- gated + tracked (existing)
%   VT_Raw_Combined.png         -- peak range bin per frame, combined scene
%   VT_Person1.png              -- per-person gated VT (existing)
%   VT_Person2.png              -- per-person gated VT (existing)
%   ToeEnvelopes_TwoPerson.png  -- stacked subplots (existing)
%   ToeEnvelopes_Overlay.png    -- NEW: P1 & P2 on same axes

% ---------- HARD-CODED PATHS ----------
empty_folder = 'C:\Users\yoginathganditi\OneDrive - California State University, Sacramento\Documents\RadarFusionGUI\BGT60TR13C\two_people_chris_empty20260317-162406\RadarIfxAvian_00';
walk_folder  = 'C:\Users\yoginathganditi\OneDrive - California State University, Sacramento\Documents\RadarFusionGUI\BGT60TR13C\two_people_chris20260317-160005\RadarIfxAvian_00';

% ---------- OPTIONS ----------
p = inputParser;
addParameter(p,'frame_dt',             0.0772688463, @isnumeric);
addParameter(p,'r_gate',               [1.0 7.0],    @isnumeric);
addParameter(p,'nfft_r',               1024,         @isnumeric);
addParameter(p,'nfft_d',               256,          @isnumeric);
addParameter(p,'min_person_sep_m',     0.40,         @isnumeric);
addParameter(p,'max_jump_m',           0.60,         @isnumeric);
addParameter(p,'dyn_gate_halfwidth_m', 0.25,         @isnumeric);
addParameter(p,'use_rx',               1,            @isnumeric);  % 1..3
addParameter(p,'save_plots',           true,         @islogical);
addParameter(p,'dpi_png',              200,          @isnumeric);
parse(p, varargin{:});
opt = p.Results;

fprintf('\n===== TWO-PERSON FIXED RADAR =====\n');
fprintf('Empty : %s\n', empty_folder);
fprintf('Walk  : %s\n', walk_folder);
fprintf('Range gate: [%.2f, %.2f] m\n', opt.r_gate(1), opt.r_gate(2));
fprintf('frame_dt : %.6f s\n\n', opt.frame_dt);

% ---------- Load .mat files ----------
bg_path = fullfile(empty_folder, 'radar.mat');
wk_path = fullfile(walk_folder,  'radar.mat');
if ~isfile(bg_path), error('Missing: %s', bg_path); end
if ~isfile(wk_path), error('Missing: %s', wk_path); end

BG = load(bg_path);
WK = load(wk_path);

% ---------- Extract radar_data ----------
% Shape: (F, R, C, S) -> permute to (F, C, R, S)
bg_raw = double(BG.radar_data);
wk_raw = double(WK.radar_data);

bg_adc = permute(bg_raw, [1 3 2 4]);   % (F, C, R, S)
wk_adc = permute(wk_raw, [1 3 2 4]);

[F, C, R, S] = size(wk_adc);
fprintf('adc shape (F,C,R,S) = [%d %d %d %d]\n', F, C, R, S);

if ~isequal(size(bg_adc), size(wk_adc))
    fprintf('WARNING: empty/walk size mismatch. Trimming to same frame count.\n');
    Fmin   = min(size(bg_adc,1), size(wk_adc,1));
    bg_adc = bg_adc(1:Fmin,:,:,:);
    wk_adc = wk_adc(1:Fmin,:,:,:);
    F = Fmin;
end

if opt.use_rx < 1 || opt.use_rx > R
    error('use_rx must be 1..%d', R);
end

% ---------- Load config for axes ----------
cfg_path = fullfile(walk_folder, 'config.json');
if ~isfile(cfg_path), error('Missing config.json in walk folder'); end
cfg_json = jsondecode(fileread(cfg_path));
cfg      = derive_cfg_params(cfg_json);
r_axis   = make_range_axis(cfg, opt.nfft_r);
v_axis   = make_velocity_axis(cfg, opt.nfft_d);

r_idx_gate = find(r_axis >= opt.r_gate(1) & r_axis <= opt.r_gate(2));
if isempty(r_idx_gate)
    error('No range bins inside r_gate [%.2f %.2f].', opt.r_gate(1), opt.r_gate(2));
end
fprintf('Range axis: %.2f to %.2f m (%d bins in gate)\n', ...
        r_axis(1), r_axis(end), numel(r_idx_gate));

% ---------- Hann windows ----------
w_r = hann(S, 'periodic');   % (S x 1)
w_d = hann(C, 'periodic');   % (C x 1)

% ============================================================
% 1. RAW RT — NO background subtraction (full range)
% ============================================================
fprintf('Computing raw RT (no bg sub)...\n');
x_nobg = squeeze(wk_adc(:,:,opt.use_rx,:));   % (F, C, S)
RT_raw_lin = zeros(opt.nfft_r, F, 'single');

for f = 1:F
    frame_cs = reshape(x_nobg(f,:,:), [C, S]);
    frame_cs = frame_cs .* (w_r.');
    Xr       = fft(frame_cs, opt.nfft_r, 2);
    RT_raw_lin(:,f) = single(mean(abs(Xr), 1));
end
RT_raw_db = 20*log10(double(RT_raw_lin) + 1e-12);

% ============================================================
% 2. RAW RT — WITH background subtraction, full range, no tracking
% ============================================================
fprintf('Computing raw RT (bg sub, no tracking)...\n');
bg_mean = mean(bg_adc, 1);            % (1, C, R, S)
wk_bs   = wk_adc - bg_mean;          % (F, C, R, S)
x       = squeeze(wk_bs(:,:,opt.use_rx,:));   % (F, C, S)

RT_bgsub_lin = zeros(opt.nfft_r, F, 'single');
for f = 1:F
    frame_cs = reshape(x(f,:,:), [C, S]);
    frame_cs = frame_cs .* (w_r.');
    Xr       = fft(frame_cs, opt.nfft_r, 2);
    RT_bgsub_lin(:,f) = single(mean(abs(Xr), 1));
end
RT_bgsub_db = 20*log10(double(RT_bgsub_lin) + 1e-12);

% ============================================================
% 3. GATED RT for tracking (existing logic)
% ============================================================
fprintf('Computing gated RT for tracking...\n');
RT_lin = RT_bgsub_lin;   % reuse bg-sub result, same data
RT_db  = RT_bgsub_db;

% ---------- Track two persons ----------
fprintf('Tracking two persons...\n');
[track1_r, track2_r] = track_two_persons_from_RT(RT_lin, r_axis, r_idx_gate, opt);

% ============================================================
% 4. RAW VT — combined scene, peak range bin per frame
% ============================================================
fprintf('Computing raw VT (combined scene, peak range bin)...\n');
VT_raw = zeros(F, opt.nfft_d, 'single');

for f = 1:F
    frame_cs = reshape(x(f,:,:), [C, S]);
    frame_cs = frame_cs .* (w_r.');
    Xr       = fft(frame_cs, opt.nfft_r, 2);   % (C, nfft_r)

    % Find the peak range bin within the gate for this frame
    gate_profile = mean(abs(Xr(:, r_idx_gate)), 1);   % (1, n_gate_bins)
    [~, peak_local] = max(gate_profile);
    peak_bin = r_idx_gate(peak_local);

    % Doppler at that range bin
    Z  = Xr(:, peak_bin);                             % (C, 1)
    D  = fftshift(fft(Z .* w_d, opt.nfft_d, 1));
    VT_raw(f,:) = single(abs(D(:))).';
end
VT_raw_db = 20*log10(double(VT_raw) + 1e-12);

% ============================================================
% 5. PER-PERSON VT (existing logic)
% ============================================================
fprintf('Computing per-person VT...\n');
vt1 = zeros(F, opt.nfft_d, 'single');
vt2 = zeros(F, opt.nfft_d, 'single');

dr        = mean(diff(r_axis));
half_bins = max(1, round(opt.dyn_gate_halfwidth_m / dr));

for f = 1:F
    frame_cs = reshape(x(f,:,:), [C, S]);
    frame_cs = frame_cs .* (w_r.');
    Xr       = fft(frame_cs, opt.nfft_r, 2);

    b1  = nearest_bin(r_axis, track1_r(f));
    rr1 = max(1,b1-half_bins):min(opt.nfft_r,b1+half_bins);
    Z1  = sum(Xr(:,rr1), 2);
    D1  = fftshift(fft(Z1 .* w_d, opt.nfft_d, 1));
    vt1(f,:) = single(abs(D1(:))).';

    b2  = nearest_bin(r_axis, track2_r(f));
    rr2 = max(1,b2-half_bins):min(opt.nfft_r,b2+half_bins);
    Z2  = sum(Xr(:,rr2), 2);
    D2  = fftshift(fft(Z2 .* w_d, opt.nfft_d, 1));
    vt2(f,:) = single(abs(D2(:))).';
end

vt1_db = 20*log10(double(vt1) + 1e-12);
vt2_db = 20*log10(double(vt2) + 1e-12);

% ---------- Toe envelopes ----------
fprintf('Computing toe envelopes...\n');
[toe1, vt1_for_toe] = safe_toe_envelope(vt1, v_axis);
[toe2, vt2_for_toe] = safe_toe_envelope(vt2, v_axis);

% ---------- Save all plots ----------
if opt.save_plots
    fprintf('Saving plots...\n');
    save_all_outputs(walk_folder, ...
        RT_raw_db, RT_bgsub_db, RT_db, r_axis, r_idx_gate, ...
        track1_r, track2_r, ...
        VT_raw_db, vt1_db, vt2_db, v_axis, ...
        toe1, toe2, opt);
end

% ---------- Return ----------
out = struct();
out.empty_folder  = empty_folder;
out.walk_folder   = walk_folder;
out.cfg           = cfg;
out.r_axis        = r_axis;
out.v_axis        = v_axis;
out.r_idx_gate    = r_idx_gate;
out.RT_raw_db     = RT_raw_db;
out.RT_bgsub_db   = RT_bgsub_db;
out.RT_db         = RT_db;
out.track1_r      = track1_r;
out.track2_r      = track2_r;
out.VT_raw_db     = VT_raw_db;
out.vt1_db        = vt1_db;
out.vt2_db        = vt2_db;
out.vt1_for_toe   = vt1_for_toe;
out.vt2_for_toe   = vt2_for_toe;
out.toe1          = toe1;
out.toe2          = toe2;

fprintf('\nDONE. 8 plots saved in:\n  %s\n', walk_folder);
end

% ============================================================
% Save all 8 plots
% ============================================================
function save_all_outputs(out_dir, ...
    RT_raw_db, RT_bgsub_db, RT_gated_db, r_axis, r_idx, ...
    track1_r, track2_r, ...
    VT_raw_db, vt1_db, vt2_db, v_axis, ...
    toe1, toe2, opt)

t_axis = (0:size(RT_raw_db,2)-1) * opt.frame_dt;

% ---- Helper: clip limits from percentiles ----
pct = @(M, lo, hi) [prctile(M(:), lo), prctile(M(:), hi)];

% --------------------------------------------------
% Plot 1: RT Raw — no background subtraction
% --------------------------------------------------
fig = figure('Visible','off','Position',[50 50 1400 500]);
cl  = pct(RT_raw_db, 35, 99.5);
imagesc(t_axis, r_axis, RT_raw_db, cl); axis xy; colorbar;
xlabel('Time (s)'); ylabel('Range (m)');
title('RT — Raw (No Background Subtraction)');
exportgraphics(fig, fullfile(out_dir,'RT_Raw_NoBgSub.png'), 'Resolution', opt.dpi_png);
close(fig);

% --------------------------------------------------
% Plot 2: RT Raw — background subtracted, full range, no tracking
% --------------------------------------------------
fig = figure('Visible','off','Position',[50 50 1400 500]);
cl  = pct(RT_bgsub_db, 35, 99.5);
imagesc(t_axis, r_axis, RT_bgsub_db, cl); axis xy; colorbar;
xlabel('Time (s)'); ylabel('Range (m)');
title('RT — Background Subtracted (No Tracking)');
exportgraphics(fig, fullfile(out_dir,'RT_Raw_BgSub.png'), 'Resolution', opt.dpi_png);
close(fig);

% --------------------------------------------------
% Plot 3: RT gated + tracked (existing)
% --------------------------------------------------
fig = figure('Visible','off','Position',[50 50 1400 500]);
gate    = RT_gated_db(r_idx,:);
cl      = pct(gate, 35, 99.5);
imagesc(t_axis, r_axis(r_idx), gate, cl); axis xy; colorbar;
xlabel('Time (s)'); ylabel('Range (m)'); title('RT — Two Person Tracks');
hold on;
plot(t_axis, track1_r, 'w-',  'LineWidth', 1.8);
plot(t_axis, track2_r, 'k--', 'LineWidth', 1.8);
legend('Person 1','Person 2','Location','northwest');
hold off;
exportgraphics(fig, fullfile(out_dir,'RT_TwoPerson_Tracks.png'), 'Resolution', opt.dpi_png);
close(fig);

% --------------------------------------------------
% Plot 4: VT Raw — combined scene, peak range bin
% --------------------------------------------------
fig = figure('Visible','off','Position',[50 50 1400 450]);
cl  = pct(VT_raw_db, 35, 99.5);
imagesc(t_axis, v_axis, VT_raw_db', cl); axis xy; colorbar;
xlabel('Time (s)'); ylabel('Velocity (m/s)');
title('VT — Raw Combined Scene (Peak Range Bin Per Frame)');
exportgraphics(fig, fullfile(out_dir,'VT_Raw_Combined.png'), 'Resolution', opt.dpi_png);
close(fig);

% --------------------------------------------------
% Plot 5: VT Person 1 (existing)
% --------------------------------------------------
fig = figure('Visible','off','Position',[50 50 1400 450]);
imagesc(t_axis, v_axis, vt1_db'); axis xy; colorbar;
xlabel('Time (s)'); ylabel('Velocity (m/s)'); title('VT — Person 1');
exportgraphics(fig, fullfile(out_dir,'VT_Person1.png'), 'Resolution', opt.dpi_png);
close(fig);

% --------------------------------------------------
% Plot 6: VT Person 2 (existing)
% --------------------------------------------------
fig = figure('Visible','off','Position',[50 50 1400 450]);
imagesc(t_axis, v_axis, vt2_db'); axis xy; colorbar;
xlabel('Time (s)'); ylabel('Velocity (m/s)'); title('VT — Person 2');
exportgraphics(fig, fullfile(out_dir,'VT_Person2.png'), 'Resolution', opt.dpi_png);
close(fig);

% --------------------------------------------------
% Plot 7: Toe Envelopes — stacked subplots (existing)
% --------------------------------------------------
fig = figure('Visible','off','Position',[50 50 1400 700]);
subplot(2,1,1);
plot(t_axis, toe1, 'k-'); grid on;
xlabel('Time (s)'); ylabel('Toe velocity (m/s)');
title('Toe Envelope — Person 1');
subplot(2,1,2);
plot(t_axis, toe2, 'k-'); grid on;
xlabel('Time (s)'); ylabel('Toe velocity (m/s)');
title('Toe Envelope — Person 2');
exportgraphics(fig, fullfile(out_dir,'ToeEnvelopes_TwoPerson.png'), 'Resolution', opt.dpi_png);
close(fig);

% --------------------------------------------------
% Plot 8: Toe Envelopes — OVERLAY (new)
% --------------------------------------------------
fig = figure('Visible','off','Position',[50 50 1400 400]);
plot(t_axis, toe1, 'b-',  'LineWidth', 1.4); hold on;
plot(t_axis, toe2, 'r--', 'LineWidth', 1.4); hold off;
grid on;
xlabel('Time (s)'); ylabel('Toe velocity (m/s)');
title('Toe Envelopes — Person 1 vs Person 2');
legend('Person 1','Person 2','Location','northeast');
exportgraphics(fig, fullfile(out_dir,'ToeEnvelopes_Overlay.png'), 'Resolution', opt.dpi_png);
close(fig);

fprintf('  Saved 8 plots to:\n  %s\n', out_dir);
end

% ============================================================
% Helpers
% ============================================================
function idx = nearest_bin(axis_vals, x)
[~, idx] = min(abs(axis_vals - x));
end

function [toe, vt_used] = safe_toe_envelope(vt_time_by_doppler, v_axis)
try
    [toe, ~] = toe_envelope(vt_time_by_doppler, v_axis);
    vt_used  = vt_time_by_doppler;
catch
    vtT     = vt_time_by_doppler.';
    [toe,~] = toe_envelope(vtT, v_axis);
    vt_used = vtT;
end
end

function [track1, track2] = track_two_persons_from_RT(RT_lin, r_axis, r_idx, opt)
nFrames  = size(RT_lin, 2);
rt_gate  = RT_lin(r_idx, :);
r_gate   = r_axis(r_idx);

track1   = nan(nFrames, 1);
track2   = nan(nFrames, 1);
MAX_HOLD = 10;
hold1    = 0; hold2 = 0;

[p1, p2] = find_two_peaks(rt_gate(:,1), r_gate, opt.min_person_sep_m);
peaks    = sort([p1, p2]);
track1(1) = peaks(1); track2(1) = peaks(2);
prev1 = track1(1);    prev2 = track2(1);

for f = 2:nFrames
    [a, b] = find_two_peaks(rt_gate(:,f), r_gate, opt.min_person_sep_m);

    if isnan(a) && isnan(b)
        hold1 = hold1+1; hold2 = hold2+1;
        if hold1 <= MAX_HOLD, track1(f) = prev1; end
        if hold2 <= MAX_HOLD, track2(f) = prev2; end
        continue;
    end

    if isnan(b)
        d1 = abs(a-prev1); d2 = abs(a-prev2);
        if d1 <= d2 && d1 <= opt.max_jump_m
            track1(f) = a; prev1 = a; hold1 = 0;
            hold2 = hold2+1; if hold2<=MAX_HOLD, track2(f)=prev2; end
        elseif d2 < d1 && d2 <= opt.max_jump_m
            track2(f) = a; prev2 = a; hold2 = 0;
            hold1 = hold1+1; if hold1<=MAX_HOLD, track1(f)=prev1; end
        else
            hold1=hold1+1; hold2=hold2+1;
            if hold1<=MAX_HOLD, track1(f)=prev1; end
            if hold2<=MAX_HOLD, track2(f)=prev2; end
        end
        continue;
    end

    cand = [a b];
    cost = [abs(cand(1)-prev1) abs(cand(2)-prev1);
            abs(cand(1)-prev2) abs(cand(2)-prev2)];
    A = cost(1,1)+cost(2,2);
    B = cost(1,2)+cost(2,1);
    if A <= B, new1=cand(1); new2=cand(2);
    else,      new1=cand(2); new2=cand(1);
    end

    if abs(new1-prev1) <= opt.max_jump_m
        track1(f)=new1; prev1=new1; hold1=0;
    else
        hold1=hold1+1; if hold1<=MAX_HOLD, track1(f)=prev1; end
    end
    if abs(new2-prev2) <= opt.max_jump_m
        track2(f)=new2; prev2=new2; hold2=0;
    else
        hold2=hold2+1; if hold2<=MAX_HOLD, track2(f)=prev2; end
    end
end

track1 = fill_nan_interp(track1);
track2 = fill_nan_interp(track2);
end

function [r1, r2] = find_two_peaks(profile, r_axis, min_sep)
r1 = nan; r2 = nan;
if all(profile==0) || all(isnan(profile)), return; end
[~,i1] = max(profile); r1 = r_axis(i1);
mask   = abs(r_axis - r1) < min_sep;
p2     = profile; p2(mask) = 0;
if all(p2==0), return; end
[~,i2] = max(p2); r2 = r_axis(i2);
if ~isnan(r1) && ~isnan(r2) && r2 < r1
    tmp=r1; r1=r2; r2=tmp;
end
end

function v = fill_nan_interp(v)
t  = (1:numel(v))';
ok = ~isnan(v);
if sum(ok) < 2, return; end
v  = interp1(t(ok), v(ok), t, 'linear', 'extrap');
end