package hermestv.launcher

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.SharedPreferences
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import android.view.KeyEvent
import android.widget.Toast
import java.io.IOException
import java.time.ZonedDateTime
import java.util.EnumMap
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * Hermes TV Home. ◀ ▶ move in a row or the dock, ▲ ▼ between the lines, OK opens, a long OK opens a small menu,
 * Back does nothing, Home moves the focus back to the first card.
 */
class HomeActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var io: ExecutorService
    private lateinit var local: ExecutorService
    private lateinit var images: ImageLoader
    private lateinit var screen: HomeScreen
    private lateinit var files: PicksFile
    private lateinit var prefs: SharedPreferences
    private lateinit var dockSource: DockAppsSource
    private lateinit var status: StatusReader
    private lateinit var weather: WeatherState
    private val client = PicksClient()
    private val rows = HomeRows(ZonedDateTime.now(Clock.zone()))
    private val states = EnumMap<Line, PickerState>(Line::class.java)
    private var apps: List<AppEntry> = emptyList()
    private var hiddenApps: List<AppEntry> = emptyList()
    private var dockSize = 0
    private var dockFocus: Int? = null
    private var menu: OpenMenu? = null
    private val ok = OkKey(menu = { menu }, open = ::open, showMenu = ::longPress, choose = ::chooseMenu)
    private var schedule: ScheduleList? = null
    private var slotOverride: String? = null
    private var picksRun = 0
    // From Hermes stays in memory only: a file could bring back picks that ended at 04:00, where the slot changes.
    @Volatile private var hermes: Picks? = null

    private val slot: String get() = slotOverride ?: rows.slot

    private val packages = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) = loadDock()
    }

    // The system tells every app about a HOME press; it also covers a TV that brings Home to the front without onNewIntent.
    private val homeKey = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.getStringExtra("reason") != "homekey") return
            Log.i(Actions.TAG, "home key")
            home()
            if (started) render(animate = false)
        }
    }
    private var started = false

    private val clockTick = object : Runnable {
        override fun run() {
            val now = ZonedDateTime.now(Clock.zone())
            screen.setClock(now)
            screen.setBanner(Schedule.line(schedule?.items.orEmpty(), now))
            if (rows.onMinute(now) && slotOverride == null) startPicks()
            main.postDelayed(this, 60_000L - System.currentTimeMillis() % 60_000L)
        }
    }

    private val statusTick = object : Runnable {
        override fun run() {
            local.execute {
                try {
                    val net = status.network()
                    val ram = status.ram()
                    main.post { screen.setStatus(net, ram) }
                } catch (e: Exception) {
                    Log.w(Actions.TAG, "status read failed: $e")
                }
            }
            main.postDelayed(this, STATUS_MS)
        }
    }

    private val weatherTick = object : Runnable {
        override fun run() {
            io.execute {
                try {
                    val w = weather.refresh()
                    main.post { screen.setWeather(w) }
                } catch (e: Exception) {
                    Log.w(Actions.TAG, "weather refresh failed: $e")
                }
            }
            main.postDelayed(this, WEATHER_MS)
        }
    }

    // An unreachable bridge counts as Hermes offline.
    private val hermesTick = object : Runnable {
        override fun run() {
            io.execute {
                val state = try {
                    client.health()
                } catch (e: Exception) {
                    Log.w(Actions.TAG, "health check failed: $e")
                    HermesState.OFF
                }
                main.post { screen.setHermes(state) }
            }
            main.postDelayed(this, HERMES_MS)
        }
    }

    private val scheduleTick = object : Runnable {
        override fun run() {
            io.execute {
                try {
                    val list = client.schedule()
                    main.post { setSchedule(list) }
                } catch (e: Exception) {
                    Log.w(Actions.TAG, "schedule refresh failed: $e")
                }
            }
            main.postDelayed(this, SCHEDULE_MS)
        }
    }

    // Bitmap pixels live outside the small Java heap, so ART can wait minutes before a GC frees the dropped images.
    private val collect = Runnable { gc() }
    private var lastGc = 0L

    // The key repeat can be late or missing; this timer makes a held OK open the menu at 600 ms.
    private val held = Runnable { ok.held(SystemClock.uptimeMillis()) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Three threads: the schedule call can wait 20 s for Hermes and must not hold up the picks.
        io = Executors.newFixedThreadPool(3)
        local = Executors.newSingleThreadExecutor()
        images = ImageLoader(this)
        files = PicksFile(filesDir)
        prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        dockSource = DockAppsSource(this)
        status = StatusReader(this)
        weather = WeatherState.of(this)
        schedule = try {
            prefs.getString(SCHEDULE_KEY, null)?.let(ScheduleList::parse)
        } catch (e: Exception) {
            Log.w(Actions.TAG, "saved schedule unreadable: $e")
            null
        }
        slotOverride = debugSlot(intent)
        screen = HomeScreen(this, images, ::dropped)
        setContentView(screen.root)
        screen.setLabel(LABEL)
        screen.render(rows, HomeRows.ROWS, animate = false)
        screen.setWeather(weather.last)
        registerReceiver(homeKey, IntentFilter(Intent.ACTION_CLOSE_SYSTEM_DIALOGS))
    }

    override fun onStart() {
        super.onStart()
        started = true
        rows.onMinute(ZonedDateTime.now(Clock.zone()))
        rows.restart()
        startPicks()
        registerReceiver(packages, IntentFilter().apply {
            addAction(Intent.ACTION_PACKAGE_ADDED)
            addAction(Intent.ACTION_PACKAGE_REMOVED)
            addAction(Intent.ACTION_PACKAGE_CHANGED)
            addDataScheme("package")
        })
        loadDock()
        clockTick.run()
        statusTick.run()
        weatherTick.run()
        hermesTick.run()
        scheduleTick.run()
        render(animate = false)
    }

    override fun onStop() {
        started = false
        picksRun++
        states.values.forEach(PickerState::stop)
        states.clear()
        closeMenu()
        main.removeCallbacksAndMessages(null)
        unregisterReceiver(packages)
        // Netflix or YouTube is in front: the home screen keeps no images in RAM; onStart loads them again.
        screen.release()
        images.clear()
        gc()
        super.onStop()
    }

    override fun onDestroy() {
        unregisterReceiver(homeKey)
        io.shutdownNow()
        local.shutdownNow()
        images.close()
        super.onDestroy()
    }

    // Home pressed while the home screen is shown.
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        Log.i(Actions.TAG, "home intent")
        home()
        val override = debugSlot(intent)
        if (override != slotOverride) {
            slotOverride = override
            startPicks()
        }
        render(animate = false)
    }

    // HOME: the focus goes back to the first card of the top line, and From Hermes is asked again at once.
    private fun home() {
        closeMenu()
        rows.home()
        dockFocus = null
        screen.focusDock(null)
        states[Line.HERMES]?.refresh()
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode in OK_KEYS) {
            if (event.repeatCount == 0) main.postDelayed(held, Press.LONG_MS)
            ok.down(event.eventTime, event.repeatCount)
            return true
        }
        val open = menu
        if (open != null) {
            when (keyCode) {
                KeyEvent.KEYCODE_DPAD_UP -> if (open.move(-1)) screen.focusMenu(open.focus)
                KeyEvent.KEYCODE_DPAD_DOWN -> if (open.move(1)) screen.focusMenu(open.focus)
                KeyEvent.KEYCODE_BACK -> closeMenu()
                KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_DPAD_RIGHT -> Unit
                else -> return super.onKeyDown(keyCode, event)
            }
            return true
        }
        when (keyCode) {
            KeyEvent.KEYCODE_DPAD_LEFT -> move(-1)
            KeyEvent.KEYCODE_DPAD_RIGHT -> move(1)
            KeyEvent.KEYCODE_DPAD_DOWN -> vertical(rows.down())
            KeyEvent.KEYCODE_DPAD_UP -> vertical(rows.up())
            KeyEvent.KEYCODE_BACK -> Unit
            else -> return super.onKeyDown(keyCode, event)
        }
        return true
    }

    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode !in OK_KEYS) return super.onKeyUp(keyCode, event)
        main.removeCallbacks(held)
        ok.up(event.eventTime, event.isCanceled)
        return true
    }

    private fun move(step: Int) {
        val d = dockFocus
        if (d != null) {
            DockApps.step(d, step, dockSize)?.let(::focusDock)
        } else if (if (step > 0) rows.next() else rows.prev()) {
            render()
        }
    }

    private fun vertical(moved: Boolean) {
        if (!moved) return
        focusDock(if (rows.focus == Line.DOCK) 0 else null)
        render()
    }

    private fun focusDock(index: Int?) {
        dockFocus = index
        screen.focusDock(index)
    }

    private fun render(refill: Collection<Line> = emptyList(), animate: Boolean = true) {
        screen.render(rows, refill, animate)
    }

    // The GC blocks the main thread: it runs once the cards rest GC_IDLE_MS after dropping images, at most every GC_GAP_MS.
    private fun dropped() {
        main.removeCallbacks(collect)
        main.postDelayed(collect, maxOf(GC_IDLE_MS, lastGc + GC_GAP_MS - SystemClock.uptimeMillis()))
    }

    private fun gc() {
        main.removeCallbacks(collect)
        lastGc = SystemClock.uptimeMillis()
        // An explicit GC blocks its caller; off the main thread a key press never waits for it.
        if (!local.isShutdown) local.execute { Runtime.getRuntime().gc() }
    }

    private fun startPicks() {
        states.values.forEach(PickerState::stop)
        states.clear()
        screen.setSubtitle(if (slot == "lunch" || slot == "dinner") MEAL_SUBTITLE else SUBTITLE)
        val timer = Timer { ms, task ->
            main.postDelayed(task, ms)
            Cancel { main.removeCallbacks(task) }
        }
        val s = slot
        val run = ++picksRun
        io.execute {
            val saved = HomeRows.ROWS.associateWith { if (it == Line.HERMES) hermes?.takeIf { h -> h.slot == s } else files.local(s, it.key) }
            main.post { if (run == picksRun) startStates(s, saved, timer) }
        }
    }

    private fun startStates(s: String, local: Map<Line, Picks?>, timer: Timer) {
        for (line in HomeRows.ROWS) {
            val saved = local[line]
            // A debug slot shows the saved lists only: the bridge always answers for the current slot.
            val fetch: () -> Picks = if (slotOverride == null) {
                { client.picks(line.key) }
            } else {
                { saved ?: throw IOException("no saved $s ${line.key}") }
            }
            val save: (Picks) -> Unit = if (line == Line.HERMES) { p -> hermes = p } else files::save
            lateinit var state: PickerState
            state = PickerState(s, saved, fetch, save, timer, io, clearOnEmpty = line == Line.HERMES) { view ->
                main.post { if (states[line] === state) show(line, view) }
            }
            states[line] = state
            state.start()
        }
    }

    private fun show(line: Line, view: PickerView) {
        if (line == Line.FILMS) screen.setLabel(if (view is PickerView.Showing) view.picks.label else LABEL)
        val focus = rows.focus
        if (!rows.show(line, view)) return
        if (rows.focus != focus) focusDock(null)
        render(listOf(line))
    }

    // PackageManager calls run on the local thread; the dock list is swapped in on the main thread.
    // A failed query keeps the dock shown, or shows Inputs and Settings when there is none yet.
    private fun loadDock() = local.execute {
        val dock = try {
            dockSource.apps()
        } catch (e: Exception) {
            Log.w(Actions.TAG, "dock query failed: $e")
            null
        }
        val tiles = DockApps.tiles(dock?.shown.orEmpty())
        main.post {
            if (dock == null && dockSize > 0) return@post
            apps = dock?.shown.orEmpty()
            hiddenApps = dock?.hidden.orEmpty()
            dockSize = tiles.size
            screen.setDock(tiles)
            focusDock(dockFocus?.coerceAtMost(tiles.lastIndex))
        }
    }

    private fun open() {
        if (rows.focus != Line.DOCK) return openRowItem()
        // No tile yet before the first dock load.
        val d = dockFocus?.takeIf { it < dockSize } ?: return
        when {
            d < apps.size -> openApp(apps[d])
            d == apps.size -> openInputs()
            else -> start(Intent(Settings.ACTION_SETTINGS), "Settings")
        }
    }

    private fun openRowItem() {
        when (val item = rows.row(rows.focus).focused) {
            is RowItem.Film -> openFilm(item.film)
            RowItem.AllFilms -> start(Intent(this, MainActivity::class.java), "All films")
            is RowItem.Message, null -> Unit
        }
    }

    private fun openFilm(film: Film) {
        if (!Actions.open(film, this)) return toast("Cannot open ${Meta.service(film.service)}")
        io.execute {
            try {
                client.opened(film)
            } catch (e: Exception) {
                Log.w(Actions.TAG, "opened not sent: $e")
            }
        }
    }

    private fun openApp(app: AppEntry) {
        if (app.pkg == DockApps.CAMERAS) {
            start(Intent(this, CamerasActivity::class.java), app.label)
            return
        }
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LEANBACK_LAUNCHER).setComponent(ComponentName(app.pkg, app.activity))
        if (start(intent, app.label)) dockSource.opened(app)
    }

    private fun longPress() {
        if (rows.focus == Line.DOCK) return dockMenu()
        val line = rows.focus
        val film = (rows.row(line).focused as? RowItem.Film)?.film ?: return
        val choices = Menus.card(film)
        if (choices.isEmpty()) return
        showMenu(film.title, choices.map { it.text }) {
            when (choices[it]) {
                Choice.NOT_FOR_US -> notForUs(line, film, channel = false)
                Choice.HIDE_CHANNEL -> notForUs(line, film, channel = true)
                else -> Unit
            }
        }
    }

    private fun dockMenu() {
        val d = dockFocus ?: return
        val app = apps.getOrNull(d)
        val choices = Menus.dock(app != null, hiddenApps.size)
        if (choices.isEmpty()) return
        showMenu(app?.label, choices.map { it.text }) {
            when (choices[it]) {
                Choice.HIDE_APP -> app?.let(::hideApp)
                Choice.SHOW_HIDDEN -> hiddenMenu()
                else -> Unit
            }
        }
    }

    private fun hiddenMenu() {
        val list = hiddenApps
        showMenu("Hidden apps", list.map { it.label } + Choice.CANCEL.text) { i ->
            list.getOrNull(i)?.let {
                dockSource.unhide(it)
                loadDock()
            }
        }
    }

    private fun hideApp(app: AppEntry) {
        dockSource.hide(app)
        loadDock()
    }

    // The item goes at once; the bridge is told in the background and keeps it out of every later list.
    private fun notForUs(line: Line, film: Film, channel: Boolean) {
        val gone = if (channel) {
            rows.row(line).items.mapNotNull { (it as? RowItem.Film)?.film }.filter { it.channel == film.channel }
        } else {
            listOf(film)
        }
        val focus = rows.focus
        gone.forEach {
            rows.remove(line, it)
            states[line]?.drop(it)
        }
        if (rows.focus != focus) focusDock(null)
        render(listOf(line))
        io.execute {
            try {
                client.hide(film, channel)
            } catch (e: Exception) {
                Log.w(Actions.TAG, "hide not sent: $e")
            }
        }
    }

    private fun showMenu(title: String?, entries: List<String>, choose: (Int) -> Unit) {
        val open = OpenMenu(entries, choose)
        menu = open
        screen.showMenu(title, entries, open.focus)
    }

    private fun chooseMenu(open: OpenMenu) {
        closeMenu()
        open.choose(open.focus)
    }

    private fun closeMenu() {
        menu = null
        screen.hideMenu()
    }

    private fun setSchedule(list: ScheduleList) {
        schedule = list
        prefs.edit().putString(SCHEDULE_KEY, list.toJson()).apply()
        screen.setBanner(Schedule.line(list.items, ZonedDateTime.now(Clock.zone())))
    }

    // The remote's Source button opens this picker; TCL's TV player is the way in when it is missing.
    private fun openInputs() {
        if (!start(Intent(INPUTS).setPackage("com.tcl.suspension"), null)) {
            start(Intent().setComponent(ComponentName("com.tcl.tv", "com.tcl.player.TVActivity")), "Inputs")
        }
    }

    /** Starts a local intent; on failure shows a toast when [name] is set. */
    private fun start(intent: Intent, name: String?): Boolean = try {
        startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        true
    } catch (e: ActivityNotFoundException) {
        notStarted(intent, name, e)
    } catch (e: SecurityException) {
        notStarted(intent, name, e)
    }

    private fun notStarted(intent: Intent, name: String?, e: Exception): Boolean {
        Log.w(Actions.TAG, "cannot start $intent: $e")
        if (name != null) toast("Cannot open $name")
        return false
    }

    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_LONG).show()

    // Screenshots of other slots need a fixed slot; a release build never takes it.
    private fun debugSlot(intent: Intent?): String? =
        if (BuildConfig.DEBUG) intent?.getStringExtra("slot")?.takeIf { it in SLOTS } else null

    companion object {
        const val LABEL = "Hermes TV"
        const val SUBTITLE = "Picked for you · Netflix and HBO Max"
        const val MEAL_SUBTITLE = "Light and easy to follow"
        const val INPUTS = "com.android.tv.action.VIEW_INPUTS"
        const val STATUS_MS = 10_000L
        const val GC_IDLE_MS = 2_000L
        const val GC_GAP_MS = 10_000L
        const val WEATHER_MS = 30 * 60_000L
        const val HERMES_MS = 60_000L
        const val SCHEDULE_MS = 30 * 60_000L
        const val PREFS = "home"
        const val SCHEDULE_KEY = "schedule"
        val OK_KEYS = setOf(KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_BUTTON_A)
        val SLOTS = setOf("morning", "lunch", "afternoon", "dinner", "night", "late")
    }
}
