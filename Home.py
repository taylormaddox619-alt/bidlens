"""App entrypoint: builds the sidebar navigation and runs the selected page.

The page scripts live in `views/`, not `pages/`. A `pages/` folder switches on Streamlit's legacy
auto-navigation, which takes over the first run after every server start (file names as menu labels, no
sections). Each view still sets up its own page config and sidebar, so it can be run on its own under
`streamlit.testing`. URLs are unchanged (`/New_Bid_Event`, ...): they come from the file names.
"""

import streamlit as st

from reload_guard import ensure_fresh

ensure_fresh()  # load current bidlens code after a redeploy (see reload_guard.py)

from bidlens import ui

navigation = st.navigation({
    section: [st.Page(path, title=title, icon=icon, default=path == "views/0_Home.py") for path, title, icon in views]
    for section, views in ui.NAV.items()
})
navigation.run()
