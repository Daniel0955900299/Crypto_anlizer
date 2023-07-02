import finplot as fplt
from functools import lru_cache
import pandas as pd
from PyQt6.QtWidgets import QComboBox, QWidget, QGridLayout, QLineEdit, QPushButton
import pyqtgraph as pg
import requests
from time import time as now, sleep
from threading import Thread
import websocket
import json

class BinanceWebsocket:
    def __init__(self):
        self.url = 'wss://stream.binance.com/stream'
        self.symbol = None
        self.interval = None
        self.ws = None
        self.df = None

    def reconnect(self, symbol, interval, df):
        self.df = df
        if symbol.lower() == self.symbol and self.interval == interval:
            return
        self.symbol = symbol.lower()
        self.interval = interval
        self.thread_connect = Thread(target=self._thread_connect)
        self.thread_connect.daemon = True
        self.thread_connect.start()

    def close(self, reset_symbol=True):
        if reset_symbol:
            self.symbol = None
        if self.ws:
            self.ws.close()
        self.ws = None

    def _thread_connect(self):
        self.close(reset_symbol=False)
        print('websocket connecting to %s...' % self.url)
        self.ws = websocket.WebSocketApp(self.url, on_message=self.on_message, on_error=self.on_error)
        self.thread_io = Thread(target=self.ws.run_forever)
        self.thread_io.daemon = True
        self.thread_io.start()
        for _ in range(100):
            if self.ws.sock and self.ws.sock.connected:
                break
            sleep(0.1)
        else:
            self.close()
            raise websocket.WebSocketTimeoutException('websocket connection failed')
        self.subscribe(self.symbol, self.interval)
        print('websocket connected')

    def subscribe(self, symbol, interval):
        try:
            data = '{"method":"SUBSCRIBE","params":["%s@kline_%s"],"id":1}' % (symbol, interval)
            self.ws.send(data)
        except Exception as e:
            print('websocket subscribe error:', type(e), e)
            raise e

    def on_message(self, *args, **kwargs):
        df = self.df
        if df is None:
            return
        msg = json.loads(args[-1])
        if 'stream' not in msg:
            return
        stream = msg['stream']
        if '@kline_' in stream:
            k = msg['data']['k']
            t = k['t']
            t1 = int(df.index[-1].timestamp()) * 1000
            if t <= t1:
                # обновления графика
                i = df.index[-1]
                df.loc[i, 'Close']  = float(k['c'])
                df.loc[i, 'High']   = max(df.loc[i, 'High'], float(k['h']))
                df.loc[i, 'Low']    = min(df.loc[i, 'Low'],  float(k['l']))
                df.loc[i, 'Volume'] = float(k['v'])
                print(k)
            else:
                # создаем новую свечу
                data = [t] + [float(k[i]) for i in ['o','c','h','l','v']]
                candle = pd.DataFrame([data], columns='Time Open Close High Low Volume'.split()).astype({'Time':'datetime64[ms]'})
                candle.set_index('Time', inplace=True)
                self.df = pd.concat([df, candle])

    def on_error(self, error, *args, **kwargs):
        print('websocket error: %s' % error)


def do_load_price_history(symbol, interval):
    url = 'https://www.binance.com/api/v1/klines?symbol=%s&interval=%s&limit=%s' % (symbol, interval, 1000)
    print('loading binance %s %s' % (symbol, interval))
    d = requests.get(url).json()
    df = pd.DataFrame(d, columns='Time Open High Low Close Volume a b c d e f'.split())
    df = df.astype({'Time':'datetime64[ms]', 'Open':float, 'High':float, 'Low':float, 'Close':float, 'Volume':float})
    return df.set_index('Time')


@lru_cache(maxsize=5)
def cache_load_price_history(symbol, interval):
    return do_load_price_history(symbol, interval)


def load_price_history(symbol, interval):
    df = cache_load_price_history(symbol, interval)
    # проверяет есть ли новая свеча
    t0 = df.index[-2].timestamp()
    t1 = df.index[-1].timestamp()
    t2 = t1 + (t1 - t0)
    if now() >= t2:
        df = do_load_price_history(symbol, interval)
    return df



def calc_stochastic_oscillator(df, n=14, m=3, smooth=3):
    lo = df.Low.rolling(n).min()
    hi = df.High.rolling(n).max()
    k = 100 * (df.Close-lo) / (hi-lo)
    d = k.rolling(m).mean()
    return k, d


def calc_plot_data(df, indicators):
    price = df['Open Close High Low'.split()]
    volume = df['Open Close Volume'.split()]
    ma50 = ma200 = vema24 =  rsi = stoch = stoch_s = None
    if 'few' in indicators:
        ma50  = price.Close.rolling(50).mean()
        ma200 = price.Close.rolling(200).mean()
        vema24 = volume.Volume.ewm(span=24).mean()

    plot_data = dict(price=price, volume=volume, ma50=ma50, ma200=ma200, vema24=vema24,rsi=rsi,
                     stoch=stoch, stoch_s=stoch_s)
    # for price line
    last_close = price.iloc[-1].Close
    last_col = fplt.candle_bull_color if last_close > price.iloc[-2].Close else fplt.candle_bear_color
    price_data = dict(last_close=last_close, last_col=last_col)
    return plot_data, price_data


def realtime_update_plot():
    if ws.df is None:
        return

    # cсчитает новые plotdata
    indicators = ctrl_panel.indicators.currentText().lower()
    data,price_data = calc_plot_data(ws.df, indicators)

    # сначала обновляет, чем график (for zoom rigidity)
    for k in data:
        if data[k] is not None:
            plots[k].update_data(data[k], gfx=False)
    for k in data:
        if data[k] is not None:
            plots[k].update_gfx()

    # pместо и цвет
    ax.price_line.setPos(price_data['last_close'])
    ax.price_line.pen.setColor(pg.mkColor(price_data['last_col']))


def change_asset(*args, **kwargs):
    # сохраняет зум
    fplt._savewindata(fplt.windows[0])

    symbol = symbol = ctrl_panel.symbol.currentText()
    interval = ctrl_panel.interval.currentText()
    if len(args) >= 2:
        symbol = args[0]
        interval = args[1]

    ws.close()
    ws.df = None
    df = load_price_history(symbol, interval=interval)
    ws.reconnect(symbol, interval, df)

    # возвращает графики
    ax.reset()
    axo.reset()
    ax_rsi.reset()

    # считает plot data
    indicators = ctrl_panel.indicators.currentText().lower()
    data,price_data = calc_plot_data(df, indicators)

    # для легенды
    ctrl_panel.move(100 if 'clean' in indicators else 200, 0)

    # plot data
    global plots
    plots = {}
    plots['price'] = fplt.candlestick_ochl(data['price'], ax=ax)
    plots['volume'] = fplt.volume_ocv(data['volume'], ax=axo)
    if data['ma50'] is not None:
        plots['ma50'] = fplt.plot(data['ma50'], legend='MA-50', ax=ax)
        plots['ma200'] = fplt.plot(data['ma200'], legend='MA-200', ax=ax)
        plots['vema24'] = fplt.plot(data['vema24'], color=4, legend='V-EMA-24', ax=axo)
    if data['rsi'] is not None:
        ax.set_visible(xaxis=False)
        ax_rsi.show()
        fplt.set_y_range(0, 100, ax=ax_rsi)
        fplt.add_band(30, 70, color='#6335', ax=ax_rsi)
        plots['rsi'] = fplt.plot(data['rsi'], legend='RSI', ax=ax_rsi)
        plots['stoch'] = fplt.plot(data['stoch'], color='#880', legend='Stoch', ax=ax_rsi)
        plots['stoch_s'] = fplt.plot(data['stoch_s'], color='#650', ax=ax_rsi)
    else:
        ax.set_visible(xaxis=True)
        ax_rsi.hide()

    # price line
    ax.price_line = pg.InfiniteLine(angle=0, movable=False, pen=fplt._makepen(fplt.candle_bull_body_color, style='.'))
    ax.price_line.setPos(price_data['last_close'])
    ax.price_line.pen.setColor(pg.mkColor(price_data['last_col']))
    ax.addItem(ax.price_line, ignoreBounds=True)

    # зум в ранге
    fplt.refresh()
# список криптовалют
def load_currencies():
    try:
        with open('currencies.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return cryptocurrencies  # если файла нет, возвращаем начальный список
def save_currencies(currencies):
    with open('currencies.json', 'w') as f:
        json.dump(currencies, f)


def load_favourites():
    try:
        with open('favourites.json', 'r') as f:
            favourites = json.load(f)
            favourites = [currency.replace("USDT", "") for currency in favourites]
            return favourites
    except FileNotFoundError:
        return favourite_currencies

def save_favourites(currencies):
    with open('favourites.json', 'w') as f:
        json.dump(currencies, f)



# Загружаем список при запуске программы
cryptocurrencies = load_currencies()
favourite_currencies = load_favourites()
def create_ctrl_panel(win):
    panel = QWidget(win)
    panel.move(100, 0)
    win.scene().addWidget(panel)
    layout = QGridLayout(panel)

    panel.symbol = QComboBox(panel)
    [panel.symbol.addItem(i+'USDT') for i in cryptocurrencies]
    panel.symbol.setCurrentIndex(1)
    layout.addWidget(panel.symbol, 0, 0)
    panel.symbol.currentTextChanged.connect(change_asset)

    layout.setColumnMinimumWidth(1, 30)

    panel.interval = QComboBox(panel)
    [panel.interval.addItem(i) for i in '1d 4h 1h 30m 15m 5m 1m 1s'.split()]
    panel.interval.setCurrentIndex(6)
    layout.addWidget(panel.interval, 0, 2)
    panel.interval.currentTextChanged.connect(change_asset)

    layout.setColumnMinimumWidth(3, 30)

    panel.indicators = QComboBox(panel)
    [panel.indicators.addItem(i) for i in 'No ind:Few indicators:'.split(':')]
    panel.indicators.setCurrentIndex(1)
    layout.addWidget(panel.indicators, 0, 4)
    panel.indicators.currentTextChanged.connect(change_asset)

    layout.setColumnMinimumWidth(5, 30)

    def add_currency():
        new_currency = panel.new_currency_input.text().upper()  # считывает введенную криптовалюту
        if new_currency and new_currency not in cryptocurrencies:
            cryptocurrencies.append(new_currency)
            panel.symbol.addItem(new_currency + 'USDT')
            save_currencies(cryptocurrencies)  # сохраняем обновленный списо

    panel.new_currency_input = QLineEdit(panel)  # текстовое поле для ввода новой валюты
    layout.addWidget(panel.new_currency_input, 0, 6)

    panel.add_button = QPushButton('Add', panel)  # кнопка добавления новой валюты
    layout.addWidget(panel.add_button, 0, 7)
    panel.add_button.clicked.connect(add_currency)

    def add_to_favourites():
        favourite_currency = panel.symbol.currentText()
        favourite_currency_without_usdt = favourite_currency.replace("USDT", "")
        if favourite_currency_without_usdt not in favourite_currencies:
            favourite_currencies.append(favourite_currency_without_usdt)
            panel.favourite_combo.addItem(favourite_currency)
            save_favourites(favourite_currencies)

    # Функция удаления валюты из избранного
    def remove_from_favourites():
        favourite_currency = panel.favourite_combo.currentText()
        favourite_currency_without_usdt = favourite_currency.replace("USDT", "")
        if favourite_currency_without_usdt and favourite_currency_without_usdt in favourite_currencies:
            favourite_currencies.remove(favourite_currency_without_usdt)
            panel.favourite_combo.removeItem(panel.favourite_combo.currentIndex())
            save_favourites(favourite_currencies)
            print(favourite_currency_without_usdt+" removed from favourites\n")

    # Создание кнопок для добавления и удаления валюты из избранного
    add_favourite_button = QPushButton('Dodaj do polubionych')
    remove_favourite_button = QPushButton('Usun z polubinych')

    # Создание выпадающего списка для избранных валют
    #panel.favourite_combo = QComboBox()
    panel.favourite_combo = QComboBox(panel)
    [panel.favourite_combo.addItem(i + 'USDT') for i in favourite_currencies]
    panel.symbol.setCurrentIndex(1)
    layout.addWidget(panel.favourite_combo, 0,9 )
    panel.symbol.currentTextChanged.connect(change_asset)

    layout.setColumnMinimumWidth(1, 30)
    # Привязка функций к кнопкам
    add_favourite_button.clicked.connect(add_to_favourites)
    remove_favourite_button.clicked.connect(remove_from_favourites)

    # Добавление кнопок и выпадающего списка в панель управления
    layout.addWidget(add_favourite_button, 0, 8)
    layout.addWidget(remove_favourite_button, 0, 10)

    def onChange():
        selected_currency = panel.favourite_combo.currentText()
        change_asset(selected_currency, ctrl_panel.interval.currentText())
    panel.favourite_combo.currentIndexChanged.connect(onChange)


    return panel


plots = {}
fplt.y_pad = 0.07 # pad some extra (for control panel)
fplt.max_zoom_points = 7
fplt.autoviewrestore()
ax,ax_rsi = fplt.create_plot('Бята Проект', rows=2, init_zoom_periods=300)
axo = ax.overlay()

# use websocket for real-time
ws = BinanceWebsocket()

# hide rsi chart to begin with; show x-axis of top plot
ax_rsi.hide()
ax_rsi.vb.setBackgroundColor(None) # don't use odd background color
ax.set_visible(xaxis=True)

ctrl_panel = create_ctrl_panel(ax.vb.win)
change_asset()
fplt.timer_callback(realtime_update_plot, 0.4) #обновление
fplt.show()





















