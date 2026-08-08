import os, subprocess, time, threading, tkinter as tk
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from tkinter import filedialog, messagebox, ttk

MAX_JOBS, SPEED_MULTIPLIER = 4, 6.5
active_processes, process_lock, cancel_requested = [], threading.Lock(), False

def get_video_duration(file_path):
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(result.stdout.strip())
    except:
        return 0.0

def convert_single_file(file_path, root_dir, source_root_dir, app_instance):
    global cancel_requested
    if cancel_requested: return file_path, "CANCELLED", None

    clean_display_name = os.path.relpath(file_path, root_dir)
    # Print the active file starting status message directly into the central log box
    app_instance.log_info_threadsafe(f"[CONVERTING] {clean_display_name}")

    base_path, _ = os.path.splitext(file_path)
    output_avi, log_file = base_path + ".avi", base_path + ".log"

    ffmpeg_args = [
        "ffmpeg", "-y", "-i", file_path, "-sn", "-c:a", "libmp3lame", "-ar", "48000",
        "-ab", "96k", "-ac", "2", "-c:v", "libxvid", "-crf", "28", "-vtag", "DIVX",
        "-vf", "scale=640:480", "-aspect", "16:9", "-g", "30", "-vb", "700k", output_avi
    ]

    try:
        with open(log_file, "w") as log:
            log.write(f"File: {file_path}\n")
            process = subprocess.Popen(ffmpeg_args, stdout=log, stderr=log)
            with process_lock:
                if cancel_requested:
                    process.terminate()
                    return file_path, "CANCELLED", log_file
                active_processes.append(process)
            process.wait()
            with process_lock:
                if process in active_processes: active_processes.remove(process)
    except: return file_path, "FAILED", log_file

    if cancel_requested:
        if os.path.exists(output_avi): os.remove(output_avi)
        return file_path, "CANCELLED", log_file

    if process.returncode == 0:
        target_move_dir = os.path.normpath(os.path.join(source_root_dir, os.path.relpath(os.path.dirname(file_path), root_dir)))
        os.makedirs(target_move_dir, exist_ok=True)
        try:
            os.replace(file_path, os.path.join(target_move_dir, os.path.basename(file_path)))
            status = "SUCCESS"
        except Exception as e: status = f"SUCCESS_BUT_MOVE_FAILED ({str(e)})"
    else: status = "FAILED"

    with open(log_file, "a") as log: log.write(f"CONVERSION_STATUS: {status}\n")
    return file_path, status, log_file

class ConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Bulk Legacy Video Converter")
        self.root.geometry("580x470")
        self.root.resizable(False, False)

        if os.path.exists("icon.png"):
            try:
                icon_img = tk.PhotoImage(file="icon.png")
                self.root.iconphoto(True, icon_img)
            except: pass

        tk.Label(root, text="Select Video Root Folder:", font=("Arial", 10, "bold")).pack(pady=(15, 2))
        self.frame_entry = tk.Frame(root); self.frame_entry.pack(fill="x", padx=20)
        self.entry_path = tk.Entry(self.frame_entry, font=("Arial", 10)); self.entry_path.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.btn_browse = tk.Button(self.frame_entry, text="Browse...", command=self.browse_folder); self.btn_browse.pack(side="right")

        self.txt_info = tk.Text(root, height=11, bg="#f0f0f0", state="disabled", font=("Consolas", 10), wrap="word")
        self.txt_info.pack(fill="x", padx=20, pady=15)

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(root, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill="x", padx=20, pady=(0, 2))

        self.lbl_status = tk.Label(root, text="Status: Waiting for input folder Selection...", font=("Arial", 9)); self.lbl_status.pack(pady=5)

        self.frame_buttons = tk.Frame(root); self.frame_buttons.pack(pady=10)
        self.btn_start = tk.Button(self.frame_buttons, text="Start Bulk Conversion", font=("Arial", 11, "bold"), bg="#4CAF50", fg="white", state="disabled", command=self.start_conversion_thread)
        self.btn_start.pack(side="left", padx=10)
        self.btn_stop = tk.Button(self.frame_buttons, text="Stop / Cancel", font=("Arial", 11, "bold"), bg="#f44336", fg="white", state="disabled", command=self.stop_conversion)
        self.btn_stop.pack(side="right", padx=10)
        self.target_directory, self.valid_files = "", []

    def log_info_threadsafe(self, text):
        """Safely pushes log messaging changes from background pool tasks into Tkinter Mainloop."""
        self.root.after(0, lambda: self.log_info(text))

    def log_info(self, text, clear=False):
        self.txt_info.config(state="normal")
        if clear: self.txt_info.delete("1.0", tk.END)
        self.txt_info.insert(tk.END, text + "\n"); self.txt_info.see(tk.END); self.txt_info.config(state="disabled")

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.entry_path.delete(0, tk.END); self.entry_path.insert(0, folder)
            self.target_directory = os.path.normpath(folder); self.analyze_folder()

    def analyze_folder(self):
        self.log_info("Scanning directory contents, please wait...", clear=True); self.btn_start.config(state="disabled"); self.root.update()
        all_files, source_dir = [], os.path.join(self.target_directory, "source")
        for root_path, _, files in os.walk(self.target_directory):
            if os.path.normpath(root_path).startswith(os.path.normpath(source_dir)): continue
            for f in files:
                if f.lower().endswith((".mkv", ".mp4")): all_files.append(os.path.join(root_path, f))
        self.valid_files = all_files; total_files = len(self.valid_files)
        if total_files == 0: self.log_info("No matching files found."); return
        self.log_info(f"Found {total_files} files. Probing metadata lengths..."); self.root.update()
        total_seconds = sum(get_video_duration(f) for f in self.valid_files)
        est_minutes = (total_seconds / 60) / SPEED_MULTIPLIER
        self.log_info("=" * 45, clear=True)
        self.log_info(f"Total Video Files Found : {total_files}")
        self.log_info(f"Combined Playback Time  : {str(timedelta(seconds=int(total_seconds)))}")
        self.log_info(f"Estimated Encoding Time : {str(timedelta(minutes=int(est_minutes)))}")
        self.log_info("=" * 45)
        self.lbl_status.config(text=f"Ready to process {total_files} files."); self.btn_start.config(state="normal")

    def start_conversion_thread(self):
        global cancel_requested, active_processes
        cancel_requested, active_processes = False, []
        threading.Thread(target=self.run_bulk_conversion, daemon=True).start()

    def stop_conversion(self):
        global cancel_requested, active_processes
        if messagebox.askyesno("Confirm Stop", "Are you sure you want to stop the conversion process immediately?"):
            cancel_requested = True; self.lbl_status.config(text="Stopping active threads, cleaning up logs..."); self.btn_stop.config(state="disabled")
            with process_lock:
                for process in active_processes: process.terminate()

    def run_bulk_conversion(self):
        self.btn_start.config(state="disabled"); self.btn_browse.config(state="disabled") if hasattr(self, "b_browse") else None; self.btn_stop.config(state="normal")
        master_log_path, source_root_dir = os.path.join(self.target_directory, "final_conversion_log.txt"), os.path.join(self.target_directory, "source")
        start_time, total_files, processed_count = time.time(), len(self.valid_files), 0
        success_list, failure_list, cancelled_list, log_paths = [], [], [], []
        
        # Freshly clear estimation report out of the box to print streaming items cleanly
        self.log_info("=== Starting Active Batch Conversion ===", clear=True)
        self.lbl_status.config(text=f"Converting: 0 of {total_files} files complete (0%)")

        with ThreadPoolExecutor(max_workers=MAX_JOBS) as executor:
            futures = {executor.submit(convert_single_file, f, self.target_directory, source_root_dir, self): f for f in self.valid_files}
            for future in futures:
                try:
                    file_path, status, log_file = future.result()
                    if log_file: log_paths.append(log_file)
                    processed_count += 1; clean_rel_path = os.path.relpath(file_path, self.target_directory)
                    
                    if status == "SUCCESS": 
                        success_list.append(f" - [SUCCESS] {clean_rel_path}")
                        self.log_info(f"[FINISHED] {clean_rel_path} -> Success")
                    elif status == "CANCELLED": 
                        cancelled_list.append(f" - [ABORTED] {clean_rel_path}")
                    else: 
                        failure_list.append(f" - [FAILED]  {clean_rel_path}")
                        self.log_info(f"[FINISHED] {clean_rel_path} -> FAILED")
                        
                    percent = int((processed_count / total_files) * 100); self.progress_var.set(percent)
                    if not cancel_requested: self.lbl_status.config(text=f"Converting: {processed_count} of {total_files} complete ({percent}%)")
                except: pass

        actual_elapsed_str = str(timedelta(seconds=int(time.time() - start_time)))
        for path in log_paths:
            if os.path.exists(path): os.remove(path)

        summary_lines = [
            "==================================================",
            "              CONVERSION SUMMARY REPORT",
            "==================================================",
            f"Date/Time Completed     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Total Files Found       : {total_files}",
            f"Successfully Done       : {len(success_list)}",
            f"Failed / Aborted  : {len(failure_list) + len(cancelled_list)}",
            "--------------------------------------------------",
            f"Actual Run Duration    : {actual_elapsed_str}",
            f"Execution Finished As   : {'ABORTED BY USER' if cancel_requested else 'SUCCESSFUL COMPLETION'}",
            "==================================================\n",
            "SUCCESSFULLY CONVERTED FILES:", "\n".join(success_list) if success_list else " - None",
            "\n\nFAILED / UNRESOLVED FILES:", "\n".join(failure_list) if failure_list else " - None",
        ]
        if cancelled_list: summary_lines.extend(["\n\nUSER CANCELLED/SKIPPED FILES:", "\n".join(cancelled_list)])
        with open(master_log_path, "w", encoding="utf-8") as master_file: master_file.write("\n".join(summary_lines))

        if cancel_requested:
            self.lbl_status.config(text="Conversion cancelled by user."); messagebox.showwarning("Cancelled", "Process stopped.")
        else:
            self.lbl_status.config(text="All tasks finished! Log compiled successfully.")
            messagebox.showinfo("Batch Complete", f"Conversions Finished!\nSaved Log: final_conversion_log.txt")

        self.btn_stop.config(state="disabled"); self.progress_var.set(0); self.entry_path.delete(0, tk.END); self.log_info("Process finished. Select another folder.", True)

if __name__ == "__main__":
    root_window = tk.Tk()
    app = ConverterApp(root_window)
    root_window.mainloop()
