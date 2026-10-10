import java.awt.BorderLayout;
import java.awt.Color;
import java.awt.Dimension;
import java.awt.Graphics;
import java.awt.Graphics2D;
import java.awt.RenderingHints;

import javax.swing.JButton;
import javax.swing.JFrame;
import javax.swing.JPanel;
import javax.swing.JTextField;
import javax.swing.SwingUtilities;

/**
 * The two boundaries this project documents, as a running application.
 *
 * <p>Written for pyjab-mcp's end-to-end layer, which had no way to test what its own README
 * tells users: that a canvas-drawn interface has no accessible children (so a screenshot is
 * all there is), and that two elements can share an accessible name (which is why a handle
 * carries a position as well as a name).
 *
 * <p>pyjab's own test application has no canvas and no duplicate names, and adding them there
 * would be a change to a different repository for the sake of a test in this one. This file is
 * forty lines and belongs here.
 *
 * <p>The three controls are deliberately neighbours:
 *
 * <ul>
 *   <li>{@code Canvas panel} paints shapes and contains no child components at all -- the
 *       accessible tree has a leaf, not a subtree, and the pixels come from paintComponent;
 *   <li>{@code Duplicate field} is the accessible name of <em>two</em> text fields with
 *       different contents, so a search by name has to return both and a handle has to be
 *       able to tell them apart by where they are;
 *   <li>{@code Ordinary button} is there so a test can distinguish "the canvas has nothing to
 *       offer" from "the window could not be read".
 * </ul>
 *
 * <p>Built for Java 8: the end-to-end job runs JDK 17, and the diagnostics matrix runs 8, so
 * staying compatible costs nothing and keeps the option open.
 */
public final class BoundaryApp {

    /** The window title, overridable so several instances can run at once. */
    private static final String DEFAULT_TITLE = "pyjab-mcp boundary app";

    public static void main(String[] args) {
        String title = DEFAULT_TITLE;
        for (String argument : args) {
            if (argument.startsWith("--title=")) {
                title = argument.substring("--title=".length());
            }
        }
        final String windowTitle = title;
        SwingUtilities.invokeLater(new Runnable() {
            @Override
            public void run() {
                build(windowTitle);
            }
        });
    }

    private static void build(String title) {
        JFrame frame = new JFrame(title);
        frame.setName("Boundary frame");
        frame.setDefaultCloseOperation(JFrame.EXIT_ON_CLOSE);
        frame.setLayout(new BorderLayout());

        Canvas panel = new Canvas();
        panel.setName("Canvas panel");
        panel.setPreferredSize(new Dimension(260, 140));
        frame.add(panel, BorderLayout.CENTER);

        JPanel controls = new JPanel();
        controls.setName("Controls panel");

        JTextField first = new JTextField("first value", 10);
        first.setName("Duplicate field");
        JTextField second = new JTextField("second value", 10);
        second.setName("Duplicate field");
        JButton button = new JButton("Ordinary button");
        button.setName("Ordinary button");

        controls.add(first);
        controls.add(second);
        controls.add(button);
        frame.add(controls, BorderLayout.SOUTH);

        frame.pack();
        frame.setLocation(120, 120);
        frame.setVisible(true);
    }

    /**
     * A panel that paints its own interface, which is the case Java Access Bridge cannot see
     * into: it is one accessible element with no children, whatever the picture shows.
     */
    static final class Canvas extends JPanel {

        private static final long serialVersionUID = 1L;

        @Override
        protected void paintComponent(Graphics graphics) {
            super.paintComponent(graphics);
            Graphics2D g = (Graphics2D) graphics;
            g.setRenderingHint(
                    RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
            g.setColor(new Color(40, 90, 160));
            g.fillRoundRect(16, 16, 96, 60, 18, 18);
            g.setColor(new Color(200, 70, 60));
            g.fillOval(130, 28, 72, 72);
            g.setColor(new Color(60, 60, 60));
            g.drawString("painted, not a component", 20, 116);
        }
    }
}
