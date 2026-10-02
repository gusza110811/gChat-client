#!/usr/bin/python3
import socket
import tkinter
import threading
from tkinter import scrolledtext
from collections import deque
import time
import datetime
import shlex
import difflib
import asyncio
import sys

try:
    import desktop_notifier  # type: ignore[import-not-found]
except ImportError:
    sys.exit("[ERROR] desktop_notifier is required for desktop notifications. Please install it using `pip install desktop-notifier`")

try:
    import dns.resolver  # type: ignore[import-not-found]
except ImportError:
    sys.exit("[ERROR] dnspython is required for SRV record resolution. Please install it using `pip install dnspython`")

class UI:

    def onSend(self,event:tkinter.Event,textvar:tkinter.Variable):
        textvar.set("")

    def __init__(self):
        self.running = True
        self.root = tkinter.Tk()
        self.root.wm_title("gChat Client")
        root = self.root

        self.chat = scrolledtext.ScrolledText(root)
        self.chat.config(state=tkinter.DISABLED)
        self.chat.pack(expand=True,fill="both")

        self.inputbar = tkinter.Frame(root)
        inputbar = self.inputbar

        self.username = tkinter.Label(inputbar,text="None")

        self.inputtext = tkinter.Variable(inputbar)
        self.input = tkinter.Entry(inputbar,textvariable=self.inputtext, width=60)
        self.input.bind("<Return>", lambda e: self.onSend(e,self.inputtext))

        self.username.pack(side="left")
        self.input.pack(side="left",fill="x")

        self.inputbar.pack()

        self.pendingOp:deque[tuple] = deque()
    def loop(self):
        while self.running:
            if self.pendingOp:
                commandt = self.pendingOp.popleft()
                command = commandt[0]
                params = commandt[1]
                if command == "print":
                    wasAtBottom = self.chat.yview()[1] > 0.99
                    self.chat.config(state=tkinter.NORMAL)
                    self.chat.insert(tkinter.END,params[0])
                    if wasAtBottom:
                        self.chat.see(tkinter.END)
                    self.chat.config(state=tkinter.DISABLED)
                elif command == "insert":
                    self.chat.config(state=tkinter.NORMAL)
                    self.chat.insert(1.0,params[0])
                    self.chat.yview_scroll(1,"units")
                    self.chat.config(state=tkinter.DISABLED)
                elif command == "clear":
                    self.chat.config(state=tkinter.NORMAL)
                    self.chat.delete(1.0,tkinter.END)
                    self.chat.config(state=tkinter.DISABLED)
                elif command == "chname":
                    self.username.config(text=params[0])

            self.root.update()
    
    def sendCommand(self,command:str,params:list):
        self.pendingOp.append((command,params))

class App():
    def __init__(self, ui:UI):
        self.ui = ui
        self.preferredName = ""
        self.currentName = ""
        self.channel = ""
        self.helpMSG = """
/help : show this message
/join : change channel
/name : change name
/whereami : show current channel
/whoami : show current name
/list : list active users
/connect [server] : connect to a server
/connect [server] [port] : connect to a server on a specific port
/disconnect : leave a server

commands are fuzzy matched, so for example `conn` will work for `connect`
"""
        self.active = False
        ui.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.socket:socket.socket

        self.msgFormat = 1

        self.pingInterval = 60

        self.listenThread = threading.Thread(target=self.listen,daemon=True)

        self.CTRLstat = None
        def sending(event,textvar:tkinter.Variable):
            message = textvar.get()
            threading.Thread(target=lambda:self.onSend(event,message)).start()
            textvar.set("")
        ui.onSend = sending

        ui.sendCommand("print",["[INFO] Welcome to gChat Client!\n"])
        ui.sendCommand("print",["[INFO] Use /name to set your name\n"])
        ui.sendCommand("print",["[INFO] Use /connect to connect to a server\n"])
        ui.sendCommand("print",["[INFO] Use /help for more info\n"])
        ui.sendCommand("print",["[INFO] Try connecting to chat.gusza.fyi\n"])

        self.notifier = desktop_notifier.DesktopNotifier("gChat Client")

    def keepAlive(self):
        while self.active:
            try:
                self.socket.send(b"PING\n")
            except BrokenPipeError:
                self.ui.sendCommand("print",["[ERROR] Disconnected"])
                self.disconnect()
            time.sleep(self.pingInterval)
    
    def resolve(self, server:str, fallbackPort:int):

        try:
            answers = dns.resolver.resolve(f"_gchat._tcp.{server}", "SRV")
            record = answers[0]  # simple case
            return str(record.target).rstrip("."), record.port
        except Exception:
            return server, fallbackPort  # fallback

    def connect(self, server:str, port:int):

        server, port = self.resolve(server,port)
        print(server,port)
        try:
            self.socket = socket.create_connection((server, port),timeout=10)
        except TimeoutError:
            self.ui.sendCommand("print",[f"[ERROR] Failed to connect to {server} : Connection timed out\n"])
            return
        except socket.gaierror:
            self.ui.sendCommand("print",[f"[ERROR] Unknown server\n"])
            return
        except OSError:
            self.ui.sendCommand("print",[f"[ERROR] Failed to connect\n"])
            return
        self.active = True
        sock = self.socket

        sock.settimeout(self.pingInterval*1.01)

        self.listenThread = threading.Thread(target=self.listen, daemon=True)
        self.listenThread.start()
        self.keepAliveThread = threading.Thread(target=self.keepAlive, daemon=True)
        self.keepAliveThread.start()

        if self.preferredName:
            self.changeName(self.preferredName)
    def disconnect(self):
        self.active = False
        try:
            self.socket.send(b"QUIT\n")
            self.socket.close()
        except Exception as e:
            print(e)
        self.ui.sendCommand("print",[f"[INFO] Disconnected\n"])
        if self.preferredName:
            self.ui.sendCommand("chname",[f"({self.preferredName})"])
        else:
            self.ui.sendCommand("chname",["None"])
    
    def mention(self, sender:str, channel:str, message:str):
        self.ui.sendCommand("print",[f"[NOTIF] You were mentioned by {sender} in {channel}\n"])
        if self.notifier:
            asyncio.run(self.notifier.send("gChat Mention", f"You were mentioned by {sender} in {channel}", timeout=5))

    def onSend(self, event, message:str):
        if not message.startswith("/"):
            if not self.active:
                self.ui.sendCommand("print",["[ERROR] Not connected to a server\n"])
                return
            try:
                self.socket.send(f"MSG {message}\n".encode())
            except BrokenPipeError:
                self.ui.sendCommand("print",["[ERROR] Connection failed\n"])
                self.disconnect()
            except AttributeError:
                self.active = False
                self.ui.sendCommand("print",["[ERROR] Not connected\n"])
            return
        self.handleCommand(message[1:])
    
    def handleCommand(self, prompt):
        command, *params = shlex.split(prompt)
        command = difflib.get_close_matches(command, ["join", "whereami", "whoami", "name", "connect", "disconnect", "list", "help"], n=1, cutoff=0.6)
        if not command:
            self.ui.sendCommand("print",["[ERROR] Invalid command, use /help for list of commands\n"])
            return
        command = command[0]
        self.ui.sendCommand("print",[f"[COMMAND] /{command} {' '.join(params)}\n"])
        if command == "join":
            try:
                self.changeCh(params[0])
            except IndexError:
                self.ui.sendCommand("print",["[ERROR] Please provide a channel\n"])
        elif command == "whereami":
            if not self.active:
                self.ui.sendCommand("print",["[ERROR] Not connected to a server\n"])
                return
            self.ui.sendCommand("print",[f"[INFO] You are in {self.channel}\n"])
        elif command == "whoami":
            if not self.active:
                self.ui.sendCommand("print",["[ERROR] Not connected to a server\n"])
                return
            self.ui.sendCommand("print",[f"[INFO] Your name is {self.currentName}\n"])
        elif command == "name":
            if params:
                self.changeName(params[0])
            else:
                self.preferredName = ""
                self.ui.sendCommand("print",[f"[INFO] Preferred name reset\n"])
                self.ui.sendCommand("print",[f"[INFO] Server name is {self.currentName}\n"])
        elif command == "connect":
            try:
                server = params[0]
            except IndexError:
                self.ui.sendCommand("print",["[ERROR] please provide host name\n"])
                return
            self.ui.sendCommand("print",[f"[INFO] Connecting to {server}\n"])
            try:
                port = int(params[1])
            except IndexError:
                port = 3355
            except ValueError:
                self.ui.sendCommand("print",["[ERROR] Port must be a number\n"])
            self.connect(server,port)
        elif command == "disconnect":
            if not self.active:
                self.ui.sendCommand("print",["[WARN] Not connected\n"])
                return

            self.disconnect()
        elif command == "list":
            if not self.active:
                self.ui.sendCommand("print",["[ERROR] Not connected to a server\n"])
                return
            try:
                self.ui.sendCommand("print",["[INFO] User list:\n"])
                self.socket.send(b"LIST\n")
            except BrokenPipeError:
                self.ui.sendCommand("print",["[ERROR] Connection failed\n"])
                self.disconnect()
            except AttributeError:
                self.active = False
                self.ui.sendCommand("print",["[ERROR] Not connected\n"])
        elif command == "help":
            self.ui.sendCommand("print",[self.helpMSG])
        else:
            self.ui.sendCommand("print",["[ERROR] Invalid command, use /help for list of commands\n"])
    
    def fetch(self):
        self.socket.send(b"FETCHC 100\n")
    
    def changeCh(self,channel:str):
        if not self.active:
            self.ui.sendCommand("print",["[ERROR] Not connected to a server\n"])
            return
        #self.ui.sendCommand("clear",[])
        #self.channel = channel
        self.socket.send(f"JOIN {channel}\n".encode())
        #self.fetch()
        #self.ui.sendCommand("print", [f"[INFO] Now talking in {channel}\n"])
    
    def changeName(self,name:str):
        if self.active:
            self.socket.send(f"NAME {name}\n".encode())
        self.preferredName = name
        self.ui.sendCommand("chname",["(" + self.preferredName + ")"])

    def listen(self):
        sock = self.socket
        while self.active:
            buffer = b""
            while self.active:
                try:
                    chunk = sock.recv(1024)
                except ConnectionResetError:
                    self.ui.sendCommand("print",["[ERROR] Server closed the connection\n"])
                    self.disconnect()
                    break
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    self.processLine(line)
    def processLine(self,line:bytes):
        print(line)
        if line.startswith(b"CTRL"):
            subcommand, ctrl, *junk = line[5:].decode().split()
            if ctrl == "fetch" and subcommand == "begin":
                self.CTRLstat = "fetch"
            elif ctrl == "list" and subcommand == "begin":
                self.CTRLstat = "list"
            elif subcommand == "end":
                self.CTRLstat = None

        elif line.startswith(b"RECV"):
            data = line.decode()[5:]
            channel, sender, *message = data.split(";")
            channel = channel.strip()
            sender = sender.strip()
            message = ";".join(message).strip()
            if channel == self.channel:
                if self.msgFormat == 0:
                    msg = f"[{datetime.datetime.fromtimestamp(round(time.time()))}] @{sender}: {message}\n"
                elif self.msgFormat == 1:
                    msg = f"[{datetime.datetime.fromtimestamp(round(time.time()))}] <{sender}> {message}\n"
                self.ui.sendCommand("print",[msg])
            if self.currentName in message:
                self.mention(sender, channel, message)
        elif line.startswith(b"ERR"):
            err = line.decode()[4:].split()[0]
            if err == "Rejected":
                suberr = line.decode()[4:].split()[1] if len(line.decode().split()) > 1 else ""
                message = line.decode()[4:].split(None, 2)[2] if len(line.decode().split()) > 2 else ""
                if (suberr == "RejectedUsername") or (suberr == "InvalidUsername"):
                    self.ui.sendCommand("chname",[f"{self.currentName} ({self.preferredName})"])
                    self.ui.sendCommand("print",["[ERROR] Username Rejected: " + message + "\n"])
                elif suberr == "UsernameTaken": # only for compatibility with old servers, new servers will use RejectedUsername
                    self.ui.sendCommand("print",["[ERROR] Username already taken\n"])
                    self.ui.sendCommand("chname",[f"{self.currentName} ({self.preferredName})"])
                elif (suberr == "RejectedChannel") or (suberr == "InvalidChannel"):
                    self.ui.sendCommand("print",["[ERROR] Channel Rejected: " + message + "\n"])
                elif suberr == "Unauthorized":
                    self.ui.sendCommand("print",["[ERROR] Unauthorized: " + message + "\n"])
            else:
                # Generic/Unknown errors
                self.ui.sendCommand("print",["[ERROR] "+line.decode()[4:]+"\n"])
        elif line.startswith(b"NOTE"):
            note = line.decode()[5:].split("=")[0].strip()
            if note == "NAME":
                name = line.decode().split("=",1)[1].strip()
                self.currentName = name
                self.ui.sendCommand("chname",[name])
            elif note == "CH":
                channel = line.decode().split("=",1)[1].strip()
                self.channel = channel
                self.ui.sendCommand("clear",[])
                self.fetch()
                self.ui.sendCommand("print", [f"[INFO] Now talking in {channel}\n"])
            elif note == "LINE_END":
                format = line.decode().split("=",1)[1].strip().lower()
                if format == "lf":
                    pass
                else:
                    self.ui.sendCommand("print",["[WARNING] Protocol mismatch\n"])
                    self.ui.sendCommand("print",[f"[WARNING] Server uses {format}\n"])
        elif self.CTRLstat == "fetch":
            try:
                timestamp, channel, sender, *message = line.strip().decode().split(";")
            except ValueError:
                self.ui.sendCommand("print",["[INFO] Junk data found, please contact server owner\n"])
                return
            timestamp = int(timestamp.strip())
            sender = sender.strip()
            message = ";".join(message).strip()
            if self.msgFormat == 0:
                msg = f"[{datetime.datetime.fromtimestamp(round(timestamp))}] @{sender}: {message}\n"
            elif self.msgFormat == 1:
                msg = f"[{datetime.datetime.fromtimestamp(round(timestamp))}] <{sender}> {message}\n"
            else:
                msg = f"[{datetime.datetime.fromtimestamp(round(timestamp))}] <{sender}> {message}\n"
            self.ui.sendCommand("insert",[msg])
        elif self.CTRLstat == "list":
            name = line.strip().decode()
            self.ui.sendCommand("print",[f"[INFO] {name}\n"])

    def on_close(self):
        ui.running = False
        self.disconnect()
        self.ui.root.destroy()

if __name__ == "__main__":
    ui = UI()
    server = "localhost"
    app = App(ui)

    ui.loop()
