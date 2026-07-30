import tkinter as tk
import webbrowser

URL = "http://100.121.165.22:8080"

root = tk.Tk()
root.title("Web Launcher")
root.geometry("480x180")
root.minsize(320, 140)
root.resizable(True, True)

tk.Label(root, text=URL, font=("Arial", 12)).pack(expand=True)
tk.Button(root, text="Open in Default Browser",
          command=lambda: webbrowser.open(URL)).pack(pady=20)

root.after(200, lambda: webbrowser.open(URL))
root.mainloop()
