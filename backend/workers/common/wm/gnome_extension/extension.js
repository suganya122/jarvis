// Window placement for the local assistant, as a D-Bus service on the session bus.
// GNOME on Wayland doesn't let one app move another app's windows; this extension runs
// inside GNOME Shell, so it can, and only does what the assistant asks (list, place,
// maximize, focus). It sends nothing anywhere. MIT licensed, like the rest of the app.

import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import Mtk from 'gi://Mtk';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'org.localassistant.Windows';
const OBJECT_PATH = '/org/localassistant/Windows';
const IFACE = `<node><interface name="org.localassistant.Windows">
  <method name="List"><arg type="s" direction="out" name="json"/></method>
  <method name="Screens"><arg type="s" direction="out" name="json"/></method>
  <method name="Place"><arg type="t" direction="in" name="id"/><arg type="i" direction="in" name="x"/>
    <arg type="i" direction="in" name="y"/><arg type="i" direction="in" name="width"/>
    <arg type="i" direction="in" name="height"/><arg type="b" direction="out" name="ok"/></method>
  <method name="Maximize"><arg type="t" direction="in" name="id"/><arg type="i" direction="in" name="monitor"/>
    <arg type="b" direction="out" name="ok"/></method>
  <method name="Focus"><arg type="t" direction="in" name="id"/><arg type="b" direction="out" name="ok"/></method>
</interface></node>`;

class Service {
    _windows() {
        return global.get_window_actors()
            .map(actor => actor.meta_window)
            .filter(w => w && w.get_window_type() === Meta.WindowType.NORMAL && !w.is_skip_taskbar());
    }

    _find(id) {
        return this._windows().find(w => String(w.get_id()) === String(id)) ?? null;
    }

    List() {
        const focus = global.display.get_focus_window();
        return JSON.stringify(this._windows().map(w => {
            const r = w.get_frame_rect();
            return {
                id: String(w.get_id()), app: w.get_wm_class() ?? '', title: w.get_title() ?? '',
                monitor: w.get_monitor(), x: r.x, y: r.y, width: r.width, height: r.height,
                minimized: w.minimized, maximized: w.get_maximized() !== 0, focused: w === focus,
            };
        }));
    }

    Screens() {
        const display = global.display;
        const screens = [];
        for (let i = 0; i < display.get_n_monitors(); i++) {
            const work = Main.layoutManager.getWorkAreaForMonitor(i);
            const full = display.get_monitor_geometry(i);
            screens.push({
                index: i, x: work.x, y: work.y, width: work.width, height: work.height,
                full_width: full.width, full_height: full.height,
                primary: i === display.get_primary_monitor(), scale: display.get_monitor_scale(i),
            });
        }
        return JSON.stringify(screens);
    }

    Place(id, x, y, width, height) {
        const w = this._find(id);
        if (!w)
            return false;
        if (w.minimized)
            w.unminimize();
        if (w.get_maximized() !== 0)
            w.unmaximize(Meta.MaximizeFlags.BOTH);
        const monitor = global.display.get_monitor_index_for_rect(
            new Mtk.Rectangle({x, y, width: Math.max(width, 1), height: Math.max(height, 1)}));
        if (monitor >= 0 && monitor !== w.get_monitor())
            w.move_to_monitor(monitor);
        w.move_resize_frame(true, x, y, width, height);
        return true;
    }

    Maximize(id, monitor) {
        const w = this._find(id);
        if (!w)
            return false;
        if (w.minimized)
            w.unminimize();
        if (monitor >= 0 && monitor !== w.get_monitor())
            w.move_to_monitor(monitor);
        w.maximize(Meta.MaximizeFlags.BOTH);
        return true;
    }

    Focus(id) {
        const w = this._find(id);
        if (!w)
            return false;
        w.activate(global.get_current_time());
        return true;
    }
}

export default class WindowPlacement extends Extension {
    enable() {
        this._service = new Service();
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this._service);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._owner = Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
            null, null, null);
    }

    disable() {
        if (this._owner)
            Gio.bus_unown_name(this._owner);
        this._dbus?.unexport();
        this._dbus = null;
        this._owner = 0;
        this._service = null;
    }
}
