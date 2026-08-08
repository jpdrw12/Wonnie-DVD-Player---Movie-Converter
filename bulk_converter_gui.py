import os, subprocess, time, threading, tkinter as tk
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox, ttk

MAX_JOBS, SPEED_MULTIPLIER = 4, 6.5
active_processes, process_lock, cancel_requested = [], threading.Lock(), False

def get_video_duration(p):
	try:
		r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', p], stdout=subprocess.PIPE, text=True)
		return float(r.stdout.strip())
	except: return 0.0

def convert_single_file(p, rd, srd):
	global cancel_requested
	if cancel_requested: return p, 'CANCELLED', None
	b, _ = os.path.splitext(p); out, logf = b + '.avi', b + '.log'
	args = ['ffmpeg', '-y', '-i', p, '-sn', '-c:a', 'libmp3lame', '-ar', '48000', '-ab', '96k', '-ac', '2', '-c:v', 'libxvid', '-crf', '28', '-vtag', 'DIVX', '-vf', 'scale=640:480', '-aspect', '16:9', '-g', '30', '-vb', '700k', out]
	try:
		with open(logf, 'w') as log:
			log.write(f'File: {p}\n')
			proc = subprocess.Popen(args, stdout=log, stderr=log)
			with process_lock:
				if cancel_requested: proc.terminate(); return p, 'CANCELLED', logf
				active_processes.append(proc)
			proc.wait()
			with process_lock:
				if proc in active_processes: active_processes.remove(proc)
	except: return p, 'FAILED', logf
	if cancel_requested:
		if os.path.exists(out): os.remove(out)
		return p, 'CANCELLED', logf
	if proc.returncode == 0:
		td = os.path.normpath(os.path.join(srd, os.path.relpath(os.path.dirname(p), rd)))
		os.makedirs(td, exist_ok=True)
		try: os.replace(p, os.path.join(td, os.path.basename(p))); s = 'SUCCESS'
		except Exception as e: s = f'SUCCESS_BUT_MOVE_FAILED ({str(e)})'
	else: s = 'FAILED'
	with open(logf, 'a') as log: log.write(f'CONVERSION_STATUS: {s}\n')
	return p, s, logf

class ConverterApp:
	def __init__(self, r):
		self.root = r; r.title('Bulk Legacy Video Converter'); r.geometry('580x460'); r.resizable(False, False)
		tk.Label(r, text='Select Video Root Folder:', font=('Arial', 10, 'bold')).pack(pady=(15, 2))
		frm = tk.Frame(r); frm.pack(fill='x', padx=20)
		self.ent = tk.Entry(frm, font=('Arial', 10)); self.ent.pack(side='left', fill='x', expand=True, padx=(0, 5))
		tk.Button(frm, text='Browse...', command=self.browse).pack(side='right')
		self.txt = tk.Text(r, height=10, bg='#f0f0f0', state='disabled', font=('Consolas', 10))
		self.txt.pack(fill='x', padx=20, pady=15)
		self.p_var = tk.DoubleVar(); ttk.Progressbar(r, variable=self.p_var, maximum=100).pack(fill='x', padx=20, pady=(0, 2))
		self.lbl = tk.Label(r, text='Status: Waiting...', font=('Arial', 9)); self.lbl.pack(pady=5)
		bfrm = tk.Frame(r); bfrm.pack(pady=10)
		self.b_start = tk.Button(bfrm, text='Start Conversion', font=('Arial', 11, 'bold'), bg='#4CAF50', fg='white', state='disabled', command=self.start_thread)
		self.b_start.pack(side='left', padx=10)
		self.b_stop = tk.Button(bfrm, text='Stop / Cancel', font=('Arial', 11, 'bold'), bg='#f44336', fg='white', state='disabled', command=self.stop)
		self.b_stop.pack(side='right', padx=10)
		self.tdir, self.vfiles = '', []
	def log(self, t, c=False):
		self.txt.config(state='normal')
		if c: self.txt.delete('1.0', tk.END)
		self.txt.insert(tk.END, t + '\n'); self.txt.see(tk.END); self.txt.config(state='disabled')
	def browse(self):
		f = filedialog.askdirectory()
		if f: self.ent.delete(0, tk.END); self.ent.insert(0, f); self.tdir = os.path.normpath(f); self.analyze()
	def analyze(self):
		self.log('Scanning directory contents...', True); self.root.update(); af = []; sd = os.path.join(self.tdir, 'source')
		for rp, _, fs in os.walk(self.tdir):
			if os.path.normpath(rp).startswith(os.path.normpath(sd)): continue
			for f in fs:
				if f.lower().endswith(('.mkv', '.mp4')): af.append(os.path.join(rp, f))
		self.vfiles = af; tot = len(af)
		if tot == 0: self.log('No matching .mkv or .mp4 found.'); return
		self.log(f'Found {tot} files. Probing lengths...'); self.root.update(); ts = sum(get_video_duration(f) for f in af)
		em = (ts / 60) / SPEED_MULTIPLIER
		self.log('='*45, True); self.log(f'Total Video Files Found : {tot}'); self.log(f'Combined Playback Time  : {str(timedelta(seconds=int(ts)))}'); self.log(f'Estimated Encoding Time : {str(timedelta(minutes=int(em)))}'); self.log('='*45)
		self.lbl.config(text=f'Ready to process {tot} files.'); self.b_start.config(state='normal')
	def start_thread(self):
		global cancel_requested, active_processes
		cancel_requested, active_processes = False, []
		threading.Thread(target=self.run, daemon=True).start()
	def stop(self):
		global cancel_requested
		if messagebox.askyesno('Confirm Stop', 'Stop conversion immediately?'):
			cancel_requested = True; self.lbl.config(text='Stopping threads...'); self.b_stop.config(state='disabled')
			with process_lock:
				for p in active_processes: p.terminate()
	def run(self):
		self.b_start.config(state='disabled'); self.b_browse.config(state='disabled') if hasattr(self, 'b_browse') else None; self.b_stop.config(state='normal')
		mlog, srd, stime, tot, pc, sl, fl, cl, lpaths = os.path.join(self.tdir, 'final_conversion_log.txt'), os.path.join(self.tdir, 'source'), time.time(), len(self.vfiles), 0, [], [], [], []
		self.lbl.config(text=f'Converting: 0 of {tot} files complete (0%)')
		with ThreadPoolExecutor(max_workers=MAX_JOBS) as ex:
			futs = {ex.submit(convert_single_file, f, self.tdir, srd): f for f in self.vfiles}
			for fut in futs:
				try:
					p, stat, lf = fut.result()
					if lf: lpaths.append(lf)
					pc += 1; cr = os.path.relpath(p, self.tdir)
					if stat == 'SUCCESS': sl.append(f' - [SUCCESS] {cr}')
					elif stat == 'CANCELLED': cl.append(f' - [ABORTED] {cr}')
					else: fl.append(f' - [FAILED]  {cr}')
					pct = int((pc / tot) * 100); self.p_var.set(pct)
					if not cancel_requested: self.lbl.config(text=f'Converting: {pc} of {tot} complete ({pct}%)')
				except: pass
		act_el = str(timedelta(seconds=int(time.time() - stime)))
		for path in lpaths:
			if os.path.exists(path): os.remove(path)
		sum_l = ['='*50, '              CONVERSION SUMMARY REPORT', '='*50, f'Date Completed : {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', f'Total Files    : {tot}', f'Success        : {len(sl)}', f'Failed/Aborted : {len(fl) + len(cl)}', '-'*50, f'Duration       : {act_el}', f'Status         : {"ABORTED" if cancel_requested else "SUCCESS"}', '='*50 + '\n', 'SUCCESSFUL CONVERSIONS:', '\n'.join(sl) if sl else ' - None', '\n\nFAILED/UNRESOLVED:', '\n'.join(fl) if fl else ' - None']
		if cl: sum_l.extend(['\n\nCANCELLED BY USER:', '\n'.join(cl)])
		with open(mlog, 'w', encoding='utf-8') as m: m.write('\n'.join(sum_l))
		if cancel_requested: self.lbl.config(text='Cancelled.'); messagebox.showwarning('Cancelled', 'Process stopped.')
		else: self.lbl.config(text='Finished'); messagebox.showinfo('Done', f'Saved Log to final_conversion_log.txt')
		self.b_stop.config(state='disabled'); self.p_var.set(0); self.ent.delete(0, tk.END); self.log('Process complete.', True)

if __name__ == "__main__":
	root = tk.Tk(); app = ConverterApp(root); root.mainloop()